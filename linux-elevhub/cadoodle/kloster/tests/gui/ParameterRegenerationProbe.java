import com.commonwealthrobotics.ActiveProject;
import com.commonwealthrobotics.controls.SelectionSession;
import com.commonwealthrobotics.kloster.ModelEditQueue;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.*;
import com.neuronrobotics.sdk.addons.kinematics.math.TransformNR;
import eu.mihosoft.vrl.v3d.*;
import eu.mihosoft.vrl.v3d.parametrics.Parameter;
import javafx.application.Platform;
import java.lang.reflect.*;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
import sun.misc.Unsafe;

/** Controlled native regeneration overlap, actual SelectionSession listeners, no Stage. */
public final class ParameterRegenerationProbe {
    public static class NativeProject extends ActiveProject {
        CaDoodleFile file;
        @Override public CaDoodleFile get(){return file;}
        @Override public Thread regenerateFrom(CaDoodleOperation source) throws FailedToApplyOperation {return file.regenerateFrom(source);}
    }
    static AtomicBoolean hold=new AtomicBoolean();
    static CountDownLatch entered=new CountDownLatch(1),release=new CountDownLatch(1);
    static ExecutorService executor;
    public static void main(String[] args) throws Exception {
        boolean baseline=args[0].equals("baseline");Path dir=Path.of(args[1]).toAbsolutePath();Files.createDirectories(dir.resolve("sources"));
        Platform.startup(()->{}); Platform.setImplicitExit(false);
        CaDoodleFile.setImageEngine((db,shapes,file)->{if(hold.compareAndSet(true,false)){entered.countDown();try{if(!release.await(30,TimeUnit.SECONDS))throw new AssertionError("native thumbnail gate timeout");}catch(InterruptedException e){throw new RuntimeException(e);}}return new javafx.scene.image.WritableImage(1,1);});
        CSG.setDefaultOptType(CSG.OptType.Manifold3d);require(CSG.getManifold()!=null,"native backend missing");
        CaDoodleFile file=new CaDoodleFile().setSelf(dir.resolve("project.doodle").toFile());file.initialize();
        try {
            String target=seed(file,dir.resolve("sources/target.stl"),new Cube(12,8,4).toCSG());
            String other=seed(file,dir.resolve("sources/other.stl"),new Cube(3,5,7).toCSG().movex(60));
            String unchanged=geometry(CaDoodleFile.getByName(file.getCurrentState(),other));
            ExtrudeSurface source=new ExtrudeSurface().setToExtrude(Set.of(target)).setSlicePlane(new TransformNR(0,0,2));join(file.addOperation(source));
            String result=source.getNamesAddedInThisOperation().get(0);
            NativeProject project=(NativeProject)unsafe().allocateInstance(NativeProject.class);project.file=file;
            SelectionSession session=(SelectionSession)unsafe().allocateInstance(SelectionSession.class);
            set(session,"ap",project);set(session,"myWatchers",new HashMap<>());executor=Executors.newFixedThreadPool(2);set(session,"executor",executor);
            set(session,"selected",new LinkedHashSet<>(List.of(CaDoodleFile.getByName(file.getCurrentState(),result))));
            if(!baseline){set(session,"parameterCommitBindings",new ConcurrentHashMap<>());set(session,"committingParameter",ThreadLocal.withInitial(()->false));}
            Method bind=SelectionSession.class.getDeclaredMethod("setUpParametrics",List.class,CaDoodleOperation.class);bind.setAccessible(true);
            fx(()->invoke(bind,session,file.getCurrentState(),source));
            Parameter para=source.inset(source.getName());
            Method commit=null;if(!baseline){commit=SelectionSession.class.getDeclaredMethod("commitParameterValue",Parameter.class,double.class);commit.setAccessible(true);}
            final Method apply=commit;
            hold.set(true);
            fx(()->{if(baseline)para.setMM(1);else invoke(apply,session,para,1.0);});
            require(entered.await(15,TimeUnit.SECONDS),"first native regeneration never reached gate");
            require(file.isRegenerating(),"thumbnail gate must hold native regeneration");
            require(close(width(file,result),14),"first native geometry must reflect offset1");
            fx(()->{if(baseline)para.setMM(2);else invoke(apply,session,para,2.0);});
            Thread.sleep(150);
            if(!baseline)require(close(para.getMM(),1),"second queued edit mutated database during first regeneration");
            release.countDown();awaitIdle(project,session,file);
            if(baseline){require(close(para.getMM(),2)&&close(width(file,result),14),"baseline lost-change reproduction did not occur");System.out.println("REPRODUCED: native DB offset2, geometry remains offset1 (width14)");}
            else {
                require(close(para.getMM(),2)&&close(width(file,result),16),"latest parameter commit was lost");
                require(unchanged.equals(geometry(CaDoodleFile.getByName(file.getCurrentState(),other))),"parameter edit changed unrelated geometry");
                // Worker checks locking at execution, even when its editor was created unlocked.
                CaDoodleFile.getByName(file.getCurrentState(),result).setIsLock(true);
                fx(()->invoke(apply,session,para,3.0));awaitIdle(project,session,file);
                require(close(para.getMM(),2)&&close(width(file,result),16),"locked parameter target changed");
                CaDoodleFile.getByName(file.getCurrentState(),result).setIsLock(false);
                Method committer=SelectionSession.class.getDeclaredMethod("parameterCommitter",Parameter.class);committer.setAccessible(true);
                AtomicReference<java.util.function.DoubleConsumer> captured=new AtomicReference<>();
                fx(()->{try{captured.set((java.util.function.DoubleConsumer)committer.invoke(session,para));}catch(Exception e){throw new RuntimeException(e);}});
                // An editor's focus loss may follow a new selection. Its valid old target remains bound.
                set(session,"selected",new LinkedHashSet<>(List.of(CaDoodleFile.getByName(file.getCurrentState(),other))));
                fx(()->captured.get().accept(3));awaitIdle(project,session,file);
                require(close(para.getMM(),3)&&close(width(file,result),18),"selection change dropped old editor commit");
                fx(()->captured.get().accept(2));awaitIdle(project,session,file);
                set(session,"selected",new LinkedHashSet<>(List.of(CaDoodleFile.getByName(file.getCurrentState(),result))));
                // Queue while a native job is active; switch project before release.
                hold.set(true);entered=new CountDownLatch(1);release=new CountDownLatch(1);
                Thread nativeThread=file.regenerateFrom(source);require(entered.await(15,TimeUnit.SECONDS),"switch gate missing");
                fx(()->invoke(apply,session,para,3.0));Thread.sleep(150);selectionResponsive(session);
                project.file=null;release.countDown();join(nativeThread);awaitIdle(project,session,file);project.file=file;
                require(close(para.getMM(),2)&&close(width(file,result),16),"stale project numeric callback mutated old file");
                // Cover the session-only busy interval after the native job has finished
                // but before myRegenerate has finished watcher bookkeeping.
                set(session,"regenerating",true);
                fx(()->captured.get().accept(3));Thread.sleep(150);
                try {selectionResponsive(session);} finally {set(session,"regenerating",false);}
                awaitIdle(project,session,file);require(close(width(file,result),18),"waited editor commit lost");
                fx(()->captured.get().accept(2));awaitIdle(project,session,file);
                Files.createDirectories(dir.resolve("replacement"));
                CaDoodleFile replacement=new CaDoodleFile().setSelf(dir.resolve("replacement/project.doodle").toFile());replacement.initialize();project.file=replacement;
                String replacementBefore=replacement.getCsgDBinstance().getDataBaseString();
                Set<String> replacementKeys=Set.copyOf(replacement.getCsgDBinstance().getDatabase().keySet());
                try {fx(()->captured.get().accept(3));awaitIdle(project,session,file);require(replacementKeys.equals(replacement.getCsgDBinstance().getDatabase().keySet())&&replacementBefore.equals(replacement.getCsgDBinstance().getDataBaseString())&&close(para.getMM(),2),"stale editor callback after project swap mutated a project");}
                finally {project.file=file;replacement.close();}
                file.save(true);CaDoodleFile reopened=CaDoodleFile.fromFile(dir.resolve("project.doodle").toFile());
                try{require(close(width(reopened,result),16),"reopened latest geometry lost");}finally{reopened.close();}
                System.out.println("PASS: rapid native numeric commits serialized, DB stable during regeneration, latest geometry16, FX selection responsive during waits, captured editor target, lock/project guards and reopen");
            }
        } finally {release.countDown();if(executor!=null)executor.shutdownNow();file.close();Platform.exit();}
    }
    static String seed(CaDoodleFile file,Path path,CSG c)throws Exception{Files.writeString(path,c.toStlString());AddFromFile a=new AddFromFile().set(path.toFile(),file).setPreventBoM(true);join(file.addOperation(a));return a.getNamesAddedInThisOperation().get(0);}
    static double width(CaDoodleFile p,String n)throws Exception{return CaDoodleFile.getByName(p.getCurrentState(),n).getTotalX();}
    static String geometry(CSG c){return c.getCenterX()+","+c.getCenterY()+","+c.getCenterZ()+","+c.getTotalX()+","+c.getTotalY()+","+c.getTotalZ();}
    static boolean close(double a,double b){return Math.abs(a-b)<.0001;}
    static void awaitIdle(ActiveProject p,SelectionSession s,CaDoodleFile f)throws Exception{long end=System.nanoTime()+30_000_000_000L;Field flag=SelectionSession.class.getDeclaredField("regenerating");flag.setAccessible(true);while(ModelEditQueue.isBusy(p)||f.isRegenerating()||f.isOperationRunning()||flag.getBoolean(s)){require(System.nanoTime()<end,"regeneration did not settle");Thread.sleep(10);}Thread.sleep(150);}
    static void join(Thread t)throws Exception{if(t!=null){t.join(30000);require(!t.isAlive(),"native operation timeout");}}
    static Unsafe unsafe()throws Exception{Field f=Unsafe.class.getDeclaredField("theUnsafe");f.setAccessible(true);return (Unsafe)f.get(null);}
    static void set(Object o,String n,Object v)throws Exception{Field f=o.getClass().getDeclaredField(n);f.setAccessible(true);f.set(o,v);}
    static void invoke(Method m,Object o,Object...args){try{m.invoke(o,args);}catch(Exception e){throw new RuntimeException(e);}}
    static void fx(Runnable r)throws Exception{CountDownLatch done=new CountDownLatch(1);AtomicReference<Throwable> failure=new AtomicReference<>();Platform.runLater(()->{try{r.run();}catch(Throwable t){failure.set(t);}finally{done.countDown();}});require(done.await(10,TimeUnit.SECONDS),"FX callback timeout");if(failure.get()!=null)throw new RuntimeException(failure.get());}
    static void selectionResponsive(SelectionSession session)throws Exception {
        Method mark=SelectionSession.class.getDeclaredMethod("markSelectionIntent");mark.setAccessible(true);
        CompletableFuture<Long> responsive=new CompletableFuture<>();
        Platform.runLater(()->{try{long previous=session.getSelectionIntentRevision();mark.invoke(session);responsive.complete(session.getSelectionIntentRevision()-previous);}catch(Throwable t){responsive.completeExceptionally(t);}});
        require(responsive.get(1000,TimeUnit.MILLISECONDS)==1,"FX selection intent did not advance while regeneration waited");
    }
    static void require(boolean ok,String m){if(!ok)throw new AssertionError(m);}
}
