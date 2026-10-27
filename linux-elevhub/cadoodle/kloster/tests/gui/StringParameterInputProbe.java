package com.commonwealthrobotics.kloster.tests.gui;

import com.commonwealthrobotics.controls.SelectionSession;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.*;
import eu.mihosoft.vrl.v3d.CSG;
import eu.mihosoft.vrl.v3d.Cube;
import eu.mihosoft.vrl.v3d.parametrics.*;
import javafx.application.Platform;
import javafx.event.ActionEvent;
import javafx.scene.*;
import javafx.scene.control.*;
import javafx.scene.input.*;
import javafx.scene.layout.GridPane;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;

/** Real native control builders/database/queue; same explicit model fixture as numeric input probe. */
public final class StringParameterInputProbe {
    static final String KEY="Fixture_CaDoodle_Mode";
    static int cases;
    public static void main(String[] args)throws Exception {
        Path root=args.length>0?Path.of(args[0]):Files.createTempDirectory("cadoodle-string-input-");Files.createDirectories(root);
        Platform.startup(()->{});List<String> failures=new ArrayList<>();
        try {
            for(KeyCode key:List.of(KeyCode.ENTER,KeyCode.TAB)) test("text-"+key,failures,()->{
                try(var f=fixture(root,"text-"+key,false)){type(f,"Hej børn æøå",key);same(f,"Hej børn æøå");require(f.callbacks().get()==1,"expected one text change");}
            });
            test("text-escape",failures,()->{try(var f=fixture(root,"escape",false)){
                type(f,"changed",KeyCode.ESCAPE);fx(()->f.editor().fireEvent(new ActionEvent()));idle(f);same(f,"down");require(f.callbacks().get()==0,"cancel changed database");
            }});
            test("empty-text",failures,()->{try(var f=fixture(root,"empty",false)){type(f,"",KeyCode.ENTER);same(f,"");}});
            test("unchanged-text",failures,()->{try(var f=fixture(root,"same",false)){type(f,"down",KeyCode.ENTER);same(f,"down");require(f.callbacks().get()==0,"same text notified database");}});
            test("disabled-text",failures,()->{try(var f=fixture(root,"disabled",false)){fx(()->f.control().getParent().setDisable(true));type(f,"changed",KeyCode.ENTER);same(f,"down");}});
            test("choice-round-trip",failures,()->{try(var f=fixture(root,"choices",true)){
                choose(f,"up");same(f,"up");choose(f,"down");same(f,"down");require(f.callbacks().get()==2,"two choices expected");
            }});
            test("empty-choice-ignored",failures,()->{try(var f=fixture(root,"clear",true)){
                fx(()->{((ComboBox<?>)f.control()).getSelectionModel().clearSelection();f.control().fireEvent(new ActionEvent());});idle(f);same(f,"down");
            }});
            test("disabled-choice",failures,()->{try(var f=fixture(root,"disabled-choice",true)){
                fx(()->f.control().getParent().setDisable(true));choose(f,"up");same(f,"down");
            }});
            test("rapid-text",failures,()->{try(var f=fixture(root,"rapid-text",false)){
                fx(()->{typeFx(f,"first",KeyCode.ENTER);typeFx(f,"last",KeyCode.TAB);require(f.editor().getText().equals("last"),"queued value disappeared");});idle(f);same(f,"last");require(f.callbacks().get()==2,"two ordered text changes expected");
            }});
            test("captured-editor-target",failures,()->{try(var f=fixture(root,"capture",false)){
                // Simulates focus leaving this field after selection has changed.
                SelectionSession session=(SelectionSession)f.control().getProperties().get("probe-session");
                fx(()->AdvancedParameterInputProbe.set(session,"selected",new LinkedHashSet<CSG>()));
                type(f,"accepted old target",KeyCode.TAB);same(f,"accepted old target");
            }});
            System.out.println("CASES: "+(cases-failures.size())+"/"+cases+" passed");require(failures.isEmpty(),String.join("; ",failures));
        } finally {Platform.exit();}
    }
    static AdvancedParameterInputProbe.Fixture fixture(Path root,String name,boolean choice)throws Exception {
        Path dir=root.resolve(name);Files.createDirectories(dir);
        var file=new AdvancedParameterInputProbe.FileFixture();file.setSelf(dir.resolve("project.doodle").toFile()).setProjectName(name);file.initialize();
        var db=file.getCsgDBinstance();var parameter=new StringParameter(db,KEY,"down",new ArrayList<>(choice?List.of("down","up"):List.of()));
        Field u=sun.misc.Unsafe.class.getDeclaredField("theUnsafe");u.setAccessible(true);var unsafe=(sun.misc.Unsafe)u.get(null);
        var session=(AdvancedParameterInputProbe.Session)unsafe.allocateInstance(AdvancedParameterInputProbe.Session.class);
        var project=(AdvancedParameterInputProbe.ProjectFixture)unsafe.allocateInstance(AdvancedParameterInputProbe.ProjectFixture.class);project.file=file;
        ExecutorService executor=Executors.newSingleThreadExecutor();
        CSG shape=new Cube(8).toCSG().setName("Fixture_shape");db.setParameter(shape,parameter);file.state=List.of(shape);
        AbstractAddFrom source=new AbstractAddFrom(){public java.io.File getFile(){return null;}public String getType(){return "string-control-fixture";}public List<CSG> process(List<CSG> state){return state;}};
        source.addNameInThisOperation(shape.getName());source.setCaDoodleFile(file);file.getOperations().add(source);
        AdvancedParameterInputProbe.set(session,"ap",project);AdvancedParameterInputProbe.set(session,"myWatchers",new HashMap<>());AdvancedParameterInputProbe.set(session,"executor",executor);
        AdvancedParameterInputProbe.set(session,"selected",new LinkedHashSet<>(List.of(shape)));
        AdvancedParameterInputProbe.set(session,"parameterCommitBindings",new ConcurrentHashMap<>());AdvancedParameterInputProbe.set(session,"committingParameter",ThreadLocal.withInitial(()->false));
        Method bind=SelectionSession.class.getDeclaredMethod("setUpParametrics",List.class,CaDoodleOperation.class);bind.setAccessible(true);
        fx(()->bind.invoke(session,file.getCurrentState(),source));
        AtomicInteger callbacks=new AtomicInteger();db.addParameterListener(KEY,(n,p)->callbacks.incrementAndGet());
        GridPane[] grid={null};
        fx(()->{
            grid[0]=new GridPane();
            Method setup=choice?SelectionSession.class.getDeclaredMethod("setUpComboBoxParametrics",GridPane.class,int.class,String.class,Parameter.class,ArrayList.class,int.class)
                    :SelectionSession.class.getDeclaredMethod("setUpTextBoxEnterData",GridPane.class,int.class,String.class,Parameter.class,int.class);
            setup.setAccessible(true);
            if(choice)setup.invoke(session,grid[0],0,"Mode",parameter,parameter.getOptions(),170);else setup.invoke(session,grid[0],0,"Text",parameter,170);
        });
        Node[] node={null};fx(()->{new Scene(grid[0]);grid[0].applyCss();grid[0].layout();node[0]=grid[0].getChildren().getLast();node[0].getProperties().put("probe-session",session);});
        return new AdvancedParameterInputProbe.Fixture(file,project,parameter,node[0],callbacks,executor);
    }
    static void type(AdvancedParameterInputProbe.Fixture f,String value,KeyCode key)throws Exception{fx(()->typeFx(f,value,key));idle(f);}
    static void typeFx(AdvancedParameterInputProbe.Fixture f,String value,KeyCode key){TextField field=f.editor();field.setText(value);field.fireEvent(new KeyEvent(KeyEvent.KEY_PRESSED,"","",key,false,false,false,false));}
    @SuppressWarnings("unchecked") static void choose(AdvancedParameterInputProbe.Fixture f,String value)throws Exception{fx(()->((ComboBox<String>)f.control()).getSelectionModel().select(value));idle(f);}
    static void same(AdvancedParameterInputProbe.Fixture f,String value){require(Objects.equals(f.parameter().getStrValue(),value),"expected "+value+" but got "+f.parameter().getStrValue());}
    static void idle(AdvancedParameterInputProbe.Fixture f)throws Exception{AdvancedParameterInputProbe.idle(f);}
    static void fx(AdvancedParameterInputProbe.Action a)throws Exception{AdvancedParameterInputProbe.fx(a);}
    static void test(String name,List<String> failures,AdvancedParameterInputProbe.Action a){cases++;try{a.run();System.out.println("PASS: "+name);}catch(Throwable t){failures.add(name+": "+t);System.out.println("FAIL: "+name+": "+t);}}
    static void require(boolean ok,String message){if(!ok)throw new AssertionError(message);}
}
