package com.commonwealthrobotics.kloster;

import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.function.Consumer;
import java.util.jar.JarFile;
import java.util.regex.Pattern;
import javafx.application.Platform;
import javafx.embed.swing.SwingFXUtils;
import javafx.fxml.FXMLLoader;
import javafx.geometry.Bounds;
import javafx.scene.*;
import javafx.scene.control.*;
import javafx.scene.layout.*;
import javax.imageio.ImageIO;

/** Real packaged FXML/skins/CSS inspector measurement. Sanitized handlers only; no Stage or desktop input. */
public final class ParameterInspectorLayoutProbe {
    static final String FXML="com/commonwealthrobotics/MainWindow.fxml";
    static final Pattern HANDLER=Pattern.compile("\\s+(?:on[A-Za-z]+|fx:controller)=\"[^\"]*\"");
    static final List<String> ACTIONS=List.of("copyButton","pasteButton","deleteButton","undoButton","redoButton","groupButton","ungroupButton","alignButton","mirronButton","lockButton","hideSHow");
    static final List<String> NAMES=List.of("Bredde","Højde","Navn","Retning","Radius","Vinkel","Forskydning","Mellemrum","Rundingsstørrelse","Antal","Figurtekst","Mønstertype");
    public static void main(String[] args)throws Exception {
        Path jar=Path.of(args[0]).toAbsolutePath(),out=Path.of(args[1]).toAbsolutePath();Files.createDirectories(out);
        Path css=args.length>2?Path.of(args[2]).toAbsolutePath():null;
        CompletableFuture<Void> done=new CompletableFuture<>();Platform.setImplicitExit(false);
        Platform.startup(()->{try{List<String> failures=new ArrayList<>();for(int[] size:new int[][]{{1024,768},{1366,768}}){try{probe(jar,out,css,size[0],size[1]);}catch(Throwable t){failures.add(size[0]+"x"+size[1]+": "+t.getMessage());System.err.println("FAIL: "+failures.getLast());}}if(!failures.isEmpty())throw new AssertionError(String.join("; ",failures));done.complete(null);}catch(Throwable t){done.completeExceptionally(t);}});
        try{done.get(90,TimeUnit.SECONDS);System.out.println("PASS: actual FXML inspector controls44px, labels, real scrolling, bounded overlay, collapse and native actions at1024x768/1366x768");}finally{Platform.exit();}
    }
    static void probe(Path jar,Path out,Path css,int width,int height)throws Exception {
        String fxml;try(JarFile archive=new JarFile(jar.toFile());InputStream in=archive.getInputStream(archive.getJarEntry(FXML))){fxml=new String(in.readAllBytes(),StandardCharsets.UTF_8);}
        FXMLLoader loader=new FXMLLoader(new URL("jar:"+jar.toUri().toURL()+"!/"+FXML));
        URLClassLoader resources=new URLClassLoader(new URL[]{jar.toUri().toURL()},ParameterInspectorLayoutProbe.class.getClassLoader());
        loader.setResources(ResourceBundle.getBundle("lang.Messages",Locale.forLanguageTag("da"),resources));
        Pane root=loader.load(new ByteArrayInputStream(HANDLER.matcher(fxml).replaceAll("").getBytes(StandardCharsets.UTF_8)));
        Map<String,ButtonBase> nativeButtons=new HashMap<>();Map<String,Integer> fired=new HashMap<>();
        for(String id:ACTIONS){ButtonBase b=node(root,id,ButtonBase.class);nativeButtons.put(id,b);fired.put(id,0);b.setOnAction(e->fired.compute(id,(k,v)->v+1));}
        for(String id:List.of("RobotLabHolder","componentTreeHolder","timelineHolder","RobotLabDrawer","componentTreeDrawer","timelineButton","drawerButton")){Node n=find(root,id);if(n!=null){n.setVisible(false);n.setManaged(false);}}
        new WorkshopLayout(root,node(root,"topBar",GridPane.class),new Button("2D-skitse"),new Button("Frihånd"),new ToggleButton("Vis linealer"),new Button("Vis pladen"),new Button("Send til printer"),new Button("Find et projekt"),null);
        VBox parametrics=node(root,"parametrics",VBox.class);GridPane grid=new GridPane();grid.setVgap(8);
        ColumnConstraints labelColumn=new ColumnConstraints(100),editorColumn=new ColumnConstraints(170);grid.getColumnConstraints().setAll(labelColumn,editorColumn);
        List<Label> labels=new ArrayList<>();List<Control> editors=new ArrayList<>();
        for(int row=0;row<NAMES.size();row++){
            // Match current native SelectionSession label factory (wrap/max100);
            // this is fixture configuration, not a claim to invoke that factory.
            Label label=new Label(NAMES.get(row));label.setWrapText(true);label.setMaxWidth(100);label.setMinWidth(50);labels.add(label);Control editor;
            switch(row%4){
                case 0->editor=ParameterNumberEditor.create(List.of(),10000,()->12,ignored->{});
                case 1->editor=ParameterNumberEditor.create(List.of("1","2","5","10","20"),10000,()->5,ignored->{});
                case 2->editor=textEditor("Elevens model");
                default->{ComboBox<String> choices=new ComboBox<>();choices.getItems().setAll("up","down");choices.setValue("down");editor=choices;}
            }
            editor.setId("parameter-inspector-fixture-"+row);editor.setMinWidth(170);editors.add(editor);grid.add(label,0,row);grid.add(editor,1,row);
        }
        parametrics.getChildren().setAll(grid);
        TitledPane shape=node(root,"shapeConfiguration",TitledPane.class);Accordion accordion=node(root,"shapeConfigurationBox",Accordion.class);shape.setAnimated(false);accordion.setExpandedPane(shape);shape.setExpanded(true);
        Scene scene=new Scene(root,width,height);if(css!=null)scene.getStylesheets().add(css.toUri().toString());root.setPrefSize(width,height);root.resize(width,height);
        layout(root);
        List<String> errors=new ArrayList<>();StringBuilder metrics=new StringBuilder("viewport="+width+"x"+height+"\n");
        metrics.append("inspector=").append(bounds(accordion)).append(" holder=").append(bounds(node(root,"shapeConfigurationHolder",Node.class))).append(" view3d=").append(bounds(node(root,"view3d",Node.class))).append('\n');
        for(int i=0;i<editors.size();i++){
            Control editor=editors.get(i);double h=bounds(editor).getHeight();metrics.append(i).append(' ').append(editor.getClass().getSimpleName()).append(" height=").append(h).append(" label=").append(bounds(labels.get(i))).append(" truncated=").append(labels.get(i).isTextTruncated()).append('\n');
            check(errors,h>=43.9,"field "+i+" height="+h+" <44px");
            check(errors,!labels.get(i).isTextTruncated(),"label cut: "+labels.get(i).getText());
            check(errors,!overlap(bounds(labels.get(i)),bounds(editor)),"label overlaps editor atrow"+i);
            if(i>0)check(errors,bounds(editors.get(i-1)).getMaxY()<=bounds(editor).getMinY()+.5,"parameter rows overlap atrow"+i);
        }
        Bounds view=bounds(node(root,"view3d",Node.class)),holder=bounds(node(root,"shapeConfigurationHolder",Node.class));
        check(errors,inside(holder,view,1),"inspector holder escapes 3D viewport: "+holder+" / "+view);
        ScrollPane scroll=ancestorScroll(editors.getLast());
        check(errors,scroll!=null,"parameter content has no actual ancestor ScrollPane");
        ImageIO.write(SwingFXUtils.fromFXImage(scene.snapshot(null),null),"png",out.resolve("inspector-top-"+width+"x"+height+".png").toFile());
        if(scroll!=null){
            scroll.setVvalue(scroll.getVmax());
            layout(root);
            Node viewport=scroll.lookup(".viewport");Bounds viewportBounds=viewport==null?bounds(scroll):bounds(viewport);
            metrics.append("scroll=").append(scroll.getId()).append(" range=").append(scroll.getVmin()).append("..").append(scroll.getVmax()).append(" vvalue=").append(scroll.getVvalue()).append(" viewport=").append(viewportBounds).append(" last=").append(bounds(editors.getLast())).append('\n');
            check(errors,scroll.getViewportBounds().getHeight()>44&&scroll.getContent().getBoundsInLocal().getHeight()>scroll.getViewportBounds().getHeight(),"fixture must exercise real vertical overflow");
            check(errors,inside(bounds(editors.getLast()),viewportBounds,1),"last field not fully reachable at actual maximum scroll");
            ImageIO.write(SwingFXUtils.fromFXImage(scene.snapshot(null),null),"png",out.resolve("inspector-bottom-"+width+"x"+height+".png").toFile());
        }
        for(String id:ACTIONS){ButtonBase original=nativeButtons.get(id);check(errors,find(root,id)==original,"native control replaced: "+id);if(!original.isDisabled()){original.fire();check(errors,fired.get(id)==1,"native action lost: "+id);}}
        double expandedHeight=accordion.getHeight();shape.setExpanded(false);accordion.setExpandedPane(null);layout(root);check(errors,!shape.isExpanded(),"shape disclosure did not collapse");
        check(errors,accordion.getHeight()<expandedHeight-20,"collapsed inspector did not release vertical overlay space");
        metrics.append("expandedInspectorHeight=").append(expandedHeight).append(" collapsedInspectorHeight=").append(accordion.getHeight()).append(" errors=").append(errors).append('\n');Files.writeString(out.resolve("metrics-"+width+"x"+height+".txt"),metrics);
        if(!errors.isEmpty())throw new AssertionError(String.join("; ",errors));
        System.out.println("PASS: inspector "+width+"x"+height+" controls="+editors.size());
    }
    static Control textEditor(String initial)throws Exception{
        try{Class<?> type=Class.forName("com.commonwealthrobotics.kloster.ParameterTextEditor");return (Control)type.getMethod("create",String.class,Consumer.class).invoke(null,initial,(Consumer<String>)ignored->{});}catch(ClassNotFoundException missing){System.out.println("FIXTURE: baseline15 lacks ParameterTextEditor; ordinary TextField measures same native free-text control type");return new TextField(initial);}
    }
    static void layout(Pane root){for(int i=0;i<4;i++){root.applyCss();root.layout();}}
    static Bounds bounds(Node n){return n.localToScene(n.getBoundsInLocal());}
    static ScrollPane ancestorScroll(Node n){for(Node p=n.getParent();p!=null;p=p.getParent())if(p instanceof ScrollPane scroll)return scroll;return null;}
    static boolean inside(Bounds a,Bounds b,double eps){return a.getMinX()>=b.getMinX()-eps&&a.getMaxX()<=b.getMaxX()+eps&&a.getMinY()>=b.getMinY()-eps&&a.getMaxY()<=b.getMaxY()+eps;}
    static boolean overlap(Bounds a,Bounds b){return Math.min(a.getMaxX(),b.getMaxX())-Math.max(a.getMinX(),b.getMinX())>.5&&Math.min(a.getMaxY(),b.getMaxY())-Math.max(a.getMinY(),b.getMinY())>.5;}
    static void check(List<String> errors,boolean okay,String detail){if(!okay)errors.add(detail);}
    static <T extends Node>T node(Node root,String id,Class<T> type){Node n=find(root,id);if(!type.isInstance(n))throw new AssertionError("Missing actual FXML node "+id);return type.cast(n);}
    static Node find(Node n,String id){if(id.equals(n.getId()))return n;if(n instanceof ScrollPane s&&s.getContent()!=null){Node f=find(s.getContent(),id);if(f!=null)return f;}if(n instanceof TitledPane t&&t.getContent()!=null){Node f=find(t.getContent(),id);if(f!=null)return f;}if(n instanceof Accordion a)for(TitledPane t:a.getPanes()){Node f=find(t,id);if(f!=null)return f;}if(n instanceof Parent p)for(Node child:p.getChildrenUnmodifiable()){Node f=find(child,id);if(f!=null)return f;}return null;}
}
