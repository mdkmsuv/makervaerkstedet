import com.commonwealthrobotics.ActiveProject;
import com.commonwealthrobotics.controls.SelectionSession;
import com.commonwealthrobotics.kloster.ModelEditQueue;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.*;
import com.neuronrobotics.sdk.addons.kinematics.math.TransformNR;
import eu.mihosoft.vrl.v3d.*;
import eu.mihosoft.vrl.v3d.parametrics.Parameter;
import eu.mihosoft.vrl.v3d.parametrics.StringParameter;
import javafx.application.Platform;
import java.lang.reflect.*;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
import java.util.function.Consumer;
import sun.misc.Unsafe;

/** Native string-mode overlap audit with actual SelectionSession listener, no Stage or replacement operations. */
public final class StringParameterRegenerationProbe {
    public static class NativeProject extends ActiveProject {
        CaDoodleFile file;
        @Override public CaDoodleFile get(){return file;}
        @Override public Thread regenerateFrom(CaDoodleOperation source)throws FailedToApplyOperation{return file.regenerateFrom(source);}
    }
    static AtomicBoolean hold=new AtomicBoolean();static AtomicInteger images=new AtomicInteger();
    static CountDownLatch entered=new CountDownLatch(1),release=new CountDownLatch(1);
    static ExecutorService executor;
    public static void main(String[] args)throws Exception {
        boolean baseline=args[0].equals("baseline");Path dir=Path.of(args[1]).toAbsolutePath();Files.createDirectories(dir.resolve("sources"));
        Platform.startup(()->{});Platform.setImplicitExit(false);
        CaDoodleFile.setImageEngine((db,shapes,file)->{images.incrementAndGet();if(hold.compareAndSet(true,false)){entered.countDown();try{require(release.await(30,TimeUnit.SECONDS),"thumbnail gate timeout");}catch(InterruptedException e){throw new RuntimeException(e);}}return new javafx.scene.image.WritableImage(1,1);});
        CSG.setDefaultOptType(CSG.OptType.Manifold3d);require(CSG.getManifold()!=null,"native Manifold3d backend unavailable");
        CaDoodleFile file=new CaDoodleFile().setSelf(dir.resolve("project.doodle").toFile()).setProjectName("String regeneration");file.initialize();
        try {
            String target=seed(file,dir.resolve("sources/target.stl"),new Cube(12,8,4).toCSG());
            String other=seed(file,dir.resolve("sources/other.stl"),new Cube(3,5,7).toCSG().movex(60));
            String sourceGeometry=geometry(get(file,target)),otherGeometry=geometry(get(file,other));
            FilletChamfer source=new FilletChamfer().setToFillet(Set.of(target)).setWorkplane(new TransformNR());join(file.addOperation(source));
            String output=source.getNamesAddedInThisOperation().get(0);
            Parameter para=file.getCsgDBinstance().get(source.getName()+"_CaDoodle_Up/Down");
            require(para instanceof StringParameter,"Up/Down must be a real StringParameter");
            require(para.getOptions().equals(new ArrayList<>(List.of("up","down"))),"native direction options changed: "+para.getOptions());
            require(para.getStrValue().equals("down"),"native default must be down");assertDirection(file,output,false);
            System.out.println("API: type="+para.getClass().getName()+" key="+para.getName()+" options="+para.getOptions()+" default="+para.getStrValue());
            NativeProject project=(NativeProject)unsafe().allocateInstance(NativeProject.class);project.file=file;
            SelectionSession session=(SelectionSession)unsafe().allocateInstance(SelectionSession.class);
            set(session,"ap",project);set(session,"myWatchers",new HashMap<>());executor=Executors.newFixedThreadPool(2);set(session,"executor",executor);
            set(session,"selected",new LinkedHashSet<>(List.of(get(file,output))));
            set(session,"parameterCommitBindings",new ConcurrentHashMap<>());set(session,"committingParameter",ThreadLocal.withInitial(()->false));
            Method bind=SelectionSession.class.getDeclaredMethod("setUpParametrics",List.class,CaDoodleOperation.class);bind.setAccessible(true);
            fx(()->invoke(bind,session,file.getCurrentState(),source));
            Consumer<String> apply;
            if(baseline)apply=para::setStrValue;
            else {
                Method factory=SelectionSession.class.getDeclaredMethod("stringParameterCommitter",Parameter.class);factory.setAccessible(true);
                AtomicReference<Consumer<String>> captured=new AtomicReference<>();
                fx(()->{try{captured.set((Consumer<String>)factory.invoke(session,para));}catch(Exception e){throw new RuntimeException(e);}});apply=captured.get();
            }
            int unchangedImages=images.get();String before=geometry(get(file,output));
            fx(()->apply.accept("down"));Thread.sleep(200);awaitIdle(project,session,file);
            require(images.get()==unchangedImages&&before.equals(geometry(get(file,output))),"unchanged string choice regenerated geometry");
            System.out.println("PASS: unchanged down is a native no-op");
            hold.set(true);fx(()->apply.accept("up"));require(entered.await(15,TimeUnit.SECONDS),"first actual native regeneration not gated");
            require(file.isRegenerating(),"gate must hold actual native regeneration");assertDirection(file,output,true);
            fx(()->apply.accept("down"));Thread.sleep(150);
            if(!baseline)require(current(file,para).getStrValue().equals("up"),"second queued string choice mutated DB during first native regeneration");
            release.countDown();awaitIdle(project,session,file);
            if(baseline){require(current(file,para).getStrValue().equals("down"),"baseline DB failed to record second choice (captured="+para.getStrValue()+", db="+current(file,para).getStrValue()+")");assertDirection(file,output,true);System.out.println("REPRODUCED: native DB down but geometry up after second choice under regeneration");}
            else {
                require(current(file,para).getStrValue().equals("down"),"latest string choice lost (captured="+para.getStrValue()+", db="+current(file,para).getStrValue()+", z="+get(file,output).getCenterZ()+")");assertDirection(file,output,false);
                // This consumer belongs to the existing editor even after selection changes.
                set(session,"selected",new LinkedHashSet<>(List.of(get(file,other))));fx(()->apply.accept("up"));awaitIdle(project,session,file);assertDirection(file,output,true);
                fx(()->apply.accept("down"));awaitIdle(project,session,file);assertDirection(file,output,false);
                get(file,output).setIsLock(true);fx(()->apply.accept("up"));awaitIdle(project,session,file);assertDirection(file,output,false);require(current(file,para).getStrValue().equals("down"),"locked string target changed DB");get(file,output).setIsLock(false);
                Files.createDirectories(dir.resolve("replacement"));CaDoodleFile replacement=new CaDoodleFile().setSelf(dir.resolve("replacement/project.doodle").toFile());replacement.initialize();
                project.file=replacement;String replacementDb=replacement.getCsgDBinstance().getDataBaseString();
                try{fx(()->apply.accept("up"));awaitIdle(project,session,file);require(replacementDb.equals(replacement.getCsgDBinstance().getDataBaseString())&&current(file,para).getStrValue().equals("down"),"old string editor mutated a swapped project");}
                finally{project.file=file;replacement.close();}
                require(sourceGeometry.equals(geometry(get(file,target)))&&otherGeometry.equals(geometry(get(file,other))),"mode changed original/unrelated geometry");
            }
            String beforeSave=geometry(get(file,output));file.save(true);CaDoodleFile reopened=CaDoodleFile.fromFile(dir.resolve("project.doodle").toFile());
            try {
                Parameter saved=reopened.getCsgDBinstance().get(para.getName());require(saved.getStrValue().equals("down"),"save/reopen lost latest native mode");assertDirection(reopened,output,false);
                if(!baseline)require(beforeSave.equals(geometry(get(reopened,output))),"save/reopen altered final direction geometry");
                Files.writeString(dir.resolve("metrics.txt"),"key="+para.getName()+" options="+para.getOptions()+" savedMode="+saved.getStrValue()+"\nbeforeSave="+beforeSave+"\nreopened="+geometry(get(reopened,output))+"\n");
                System.out.println(baseline?"OBSERVED: reopen regenerates stored down mode and repairs stale up geometry":"PASS: rapid native string choices serialized, direction down, unchanged/lock/project guards and save/reopen");
            } finally {reopened.close();}
        } finally{release.countDown();if(executor!=null)executor.shutdownNow();file.close();Platform.exit();}
    }
    static void assertDirection(CaDoodleFile p,String name,boolean up)throws Exception{CSG c=get(p,name);require(c.isHole()&&c.getTotalZ()>1.9&&c.getTotalZ()<2.1,"fillet cutting geometry degenerate");require(up?c.getCenterZ()>.9:c.getCenterZ()<-.9,"native direction incorrect, expected "+(up?"up":"down")+", centerZ="+c.getCenterZ());}
    static CSG get(CaDoodleFile p,String n)throws Exception{return CaDoodleFile.getByName(p.getCurrentState(),n);}
    static Parameter current(CaDoodleFile p,Parameter parameter){return p.getCsgDBinstance().getDatabase().get(parameter.getName());}
    static String seed(CaDoodleFile p,Path path,CSG c)throws Exception{Files.writeString(path,c.toStlString());AddFromFile a=new AddFromFile().set(path.toFile(),p).setPreventBoM(true);join(p.addOperation(a));return a.getNamesAddedInThisOperation().get(0);}
    static String geometry(CSG c){double[] v=c.getVertices();List<String> points=new ArrayList<>();for(int i=0;i<v.length;i+=3)points.add(Math.round(v[i]*10000)+","+Math.round(v[i+1]*10000)+","+Math.round(v[i+2]*10000));Collections.sort(points);return "center="+Math.round(c.getCenterX()*10000)+","+Math.round(c.getCenterY()*10000)+","+Math.round(c.getCenterZ()*10000)+" bounds="+Math.round(c.getTotalX()*10000)+","+Math.round(c.getTotalY()*10000)+","+Math.round(c.getTotalZ()*10000)+" hole="+c.isHole()+" vertices="+points;}
    static void awaitIdle(ActiveProject p,SelectionSession s,CaDoodleFile f)throws Exception{Field flag=SelectionSession.class.getDeclaredField("regenerating");flag.setAccessible(true);long end=System.nanoTime()+30_000_000_000L;while(ModelEditQueue.isBusy(p)||f.isOperationRunning()||f.isRegenerating()||flag.getBoolean(s)){require(System.nanoTime()<end,"native string job failed to settle");Thread.sleep(10);}Thread.sleep(100);}
    static void join(Thread t)throws Exception{if(t!=null){t.join(30000);require(!t.isAlive(),"native operation timeout");}}
    static Unsafe unsafe()throws Exception{Field f=Unsafe.class.getDeclaredField("theUnsafe");f.setAccessible(true);return (Unsafe)f.get(null);}
    static void set(Object o,String n,Object v)throws Exception{Field f=o.getClass().getDeclaredField(n);f.setAccessible(true);f.set(o,v);}
    static void invoke(Method m,Object o,Object...args){try{m.invoke(o,args);}catch(Exception e){throw new RuntimeException(e);}}
    static void fx(Runnable r)throws Exception{CountDownLatch done=new CountDownLatch(1);AtomicReference<Throwable> failure=new AtomicReference<>();Platform.runLater(()->{try{r.run();}catch(Throwable t){failure.set(t);}finally{done.countDown();}});require(done.await(10,TimeUnit.SECONDS),"FX callback timeout");if(failure.get()!=null)throw new RuntimeException(failure.get());}
    static void require(boolean ok,String m){if(!ok)throw new AssertionError(m);}
}
