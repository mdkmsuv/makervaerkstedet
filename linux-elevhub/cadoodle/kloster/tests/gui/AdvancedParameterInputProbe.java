package com.commonwealthrobotics.kloster.tests.gui;

import com.commonwealthrobotics.controls.SelectionSession;
import com.commonwealthrobotics.ActiveProject;
import com.commonwealthrobotics.kloster.ModelEditQueue;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.*;
import eu.mihosoft.vrl.v3d.CSG;
import eu.mihosoft.vrl.v3d.Cube;
import eu.mihosoft.vrl.v3d.parametrics.*;
import javafx.application.Platform;
import javafx.event.ActionEvent;
import javafx.scene.Node;
import javafx.scene.control.*;
import javafx.scene.input.*;
import javafx.scene.layout.GridPane;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;

/** Actual native builder and JavaFX input; isolated controls, no Stage/desktop. */
public final class AdvancedParameterInputProbe {
    static final String KEY="Fixture_CaDoodle_Value";
    static final Method SETUP;
    static int cases;
    static { try { SETUP=SelectionSession.class.getDeclaredMethod("setUpNumberChoices",
        GridPane.class,int.class,String.class,Parameter.class,int.class); SETUP.setAccessible(true);
    } catch(Exception e) { throw new ExceptionInInitializerError(e); } }
    static class Session extends SelectionSession {
        Session(){super(null,null,null);}
        @Override public boolean setKeyBindingFocus(){return true;}
    }
    static class FileFixture extends CaDoodleFile {
        List<CSG> state=List.of();
        @Override public List<CSG> getCurrentState(){return state;}
        @Override public double getPercentInitialized(){return 1;}
    }
    static class ProjectFixture extends ActiveProject {
        CaDoodleFile file;
        @Override public CaDoodleFile get(){return file;}
        @Override public Thread regenerateFrom(CaDoodleOperation source){
            Thread task=new Thread(()->{});task.start();return task;
        }
    }
    record Fixture(CaDoodleFile file,ProjectFixture project,Parameter parameter,Node control,
                   AtomicInteger callbacks,ExecutorService executor) implements AutoCloseable {
        TextField editor(){return control instanceof ComboBox<?> c?c.getEditor():(TextField)control;}
        double value(){return parameter.getMM();}
        public void close(){executor.shutdownNow();file.close();}
    }
    public static void main(String[] args) throws Exception {
        Path root=args.length>0?Path.of(args[0]):Files.createTempDirectory("cadoodle-parameter-input-");
        Files.createDirectories(root); Locale.setDefault(Locale.forLanguageTag("da-DK"));
        Platform.startup(()->{}); List<String> failures=new ArrayList<>();
        try {
            for(boolean combo:List.of(false,true)) for(String decimal:List.of("1.25","1,25"))
                for(KeyCode key:List.of(KeyCode.ENTER,KeyCode.TAB)) {
                    String name=(combo?"combo":"text")+"-"+decimal+"-"+key;
                    check(name,failures,()->{try(Fixture f=fixture(root,name,combo,false)){
                        type(f,decimal,key); near(f.value(),1.25); require(f.callbacks.get()==1,"one change expected");
                    }});
                }
            check("list-selection",failures,()->{try(Fixture f=fixture(root,"list",true,false)){
                fx(()->((ComboBox<?>)f.control).getSelectionModel().select(1)); idle(f); near(f.value(),2); require(f.callbacks.get()==1,"one list commit expected");
            }});
            check("return-to-original-list-choice",failures,()->{try(Fixture f=fixture(root,"return-list",true,false,.5)){
                type(f,"1.25",KeyCode.ENTER);near(f.value(),1.25);
                fx(()->((ComboBox<?>)f.control).getSelectionModel().select(0));idle(f);near(f.value(),.5);
            }});
            check("comma-option-clamp",failures,()->{try(Fixture f=fixture(root,"clamp",true,true)){
                type(f,"9",KeyCode.ENTER); near(f.value(),2);
                type(f,"-8",KeyCode.TAB); near(f.value(),.5);
            }});
            check("unchanged-no-regeneration",failures,()->{try(Fixture f=fixture(root,"same",false,false)){
                type(f,"1,0",KeyCode.ENTER); fx(()->f.editor().fireEvent(new ActionEvent())); idle(f);
                near(f.value(),1); require(f.callbacks.get()==0,"unchanged value notified listeners");
            }});
            check("invalid-finite-values",failures,()->{try(Fixture f=fixture(root,"invalid",false,false)){
                for(String value:List.of("NaN","Infinity","-Infinity","abc","")){type(f,value,KeyCode.ENTER); near(f.value(),1);}
                require(f.callbacks.get()==0,"invalid value notified listeners");
            }});
            check("escape-cancels",failures,()->{try(Fixture f=fixture(root,"escape",true,false)){
                type(f,"1.75",KeyCode.ESCAPE); fx(()->f.editor().fireEvent(new ActionEvent())); idle(f);
                near(f.value(),1); require(f.callbacks.get()==0,"cancel notified listeners");
            }});
            check("disabled-ancestor",failures,()->{try(Fixture f=fixture(root,"disabled",false,false)){
                fx(()->f.control.getParent().setDisable(true)); type(f,"1.75",KeyCode.ENTER);
                near(f.value(),1); require(f.callbacks.get()==0,"disabled panel edited parameter");
            }});
            System.out.println("CASES: "+(cases-failures.size())+"/"+cases+" passed");
            require(failures.isEmpty(),String.join("; ",failures));
        } finally {Platform.exit();}
    }
    static Fixture fixture(Path root,String name,boolean combo,boolean commaOptions) throws Exception {
        return fixture(root,name,combo,commaOptions,1.0);
    }
    static Fixture fixture(Path root,String name,boolean combo,boolean commaOptions,double initial) throws Exception {
        Path dir=root.resolve(name.replace(',','_')); Files.createDirectories(dir);
        FileFixture file=new FileFixture();file.setSelf(dir.resolve("project.doodle").toFile()).setProjectName(name);file.initialize();
        CSGDatabaseInstance db=file.getCsgDBinstance();
        Parameter parameter=new LengthParameter(db,KEY,initial,new ArrayList<>(combo?List.of(.5,2.0):List.of()));
        if(commaOptions){parameter.getOptions().clear();parameter.getOptions().addAll(List.of("0,5","2,0"));}
        Field f=sun.misc.Unsafe.class.getDeclaredField("theUnsafe"); f.setAccessible(true);
        Session session=(Session)((sun.misc.Unsafe)f.get(null)).allocateInstance(Session.class);
        ProjectFixture project=(ProjectFixture)((sun.misc.Unsafe)f.get(null)).allocateInstance(ProjectFixture.class);project.file=file;
        ExecutorService executor=Executors.newSingleThreadExecutor();
        CSG shape=new Cube(8).toCSG().setName("Fixture_shape");db.setParameter(shape,parameter);file.state=List.of(shape);
        AbstractAddFrom source=new AbstractAddFrom(){
            public java.io.File getFile(){return null;}
            public String getType(){return "parameter-control-fixture";}
            public List<CSG> process(List<CSG> state){return state;}
        };
        source.addNameInThisOperation(shape.getName());source.setCaDoodleFile(file);file.getOperations().add(source);
        set(session,"ap",project);set(session,"myWatchers",new HashMap<>());set(session,"executor",executor);
        set(session,"selected",new LinkedHashSet<>(List.of(shape)));
        // Added fields are absent in the immutable baseline. The actual native
        // setUpParametrics method installs production callbacks in either case.
        try {set(session,"parameterCommitBindings",new ConcurrentHashMap<>());set(session,"committingParameter",ThreadLocal.withInitial(()->false));}
        catch(NoSuchFieldException baseline) { }
        Method bind=SelectionSession.class.getDeclaredMethod("setUpParametrics",List.class,CaDoodleOperation.class);bind.setAccessible(true);
        fx(()->bind.invoke(session,file.getCurrentState(),source));
        AtomicInteger callbacks=new AtomicInteger();db.addParameterListener(KEY,(n,p)->callbacks.incrementAndGet());
        Node[] node=new Node[1];
        fx(()->{GridPane grid=new GridPane();SETUP.invoke(session,grid,0,"Value",parameter,170);node[0]=grid.getChildren().getLast();
            new javafx.scene.Scene(grid); grid.applyCss(); grid.layout();});
        return new Fixture(file,project,parameter,node[0],callbacks,executor);
    }
    static void type(Fixture f,String value,KeyCode key) throws Exception {
        fx(()->{TextField field=f.editor();field.setText(value);field.fireEvent(new KeyEvent(KeyEvent.KEY_PRESSED,"","",key,false,false,false,false));});
        idle(f);
    }
    static void set(Object target,String name,Object value)throws Exception{Field f=SelectionSession.class.getDeclaredField(name);f.setAccessible(true);f.set(target,value);}
    static void idle(Fixture f)throws Exception{long end=System.nanoTime()+10_000_000_000L;while(ModelEditQueue.isBusy(f.project)){require(System.nanoTime()<end,"numeric queue timeout");fx(()->{});Thread.sleep(5);}fx(()->{});}
    static void fx(Action action) throws Exception {FutureTask<Void> t=new FutureTask<>(()->{action.run();return null;});Platform.runLater(t);t.get(10,TimeUnit.SECONDS);}
    static void check(String name,List<String> failures,Action action){cases++;try{action.run();System.out.println("PASS: "+name);}catch(Throwable e){failures.add(name+": "+e);System.out.println("FAIL: "+name+": "+e);}}
    static void near(double actual,double expected){require(Double.isFinite(actual)&&Math.abs(actual-expected)<1e-6,actual+" != "+expected);}
    static void require(boolean test,String message){if(!test)throw new AssertionError(message);}
    interface Action{void run()throws Exception;}
}
