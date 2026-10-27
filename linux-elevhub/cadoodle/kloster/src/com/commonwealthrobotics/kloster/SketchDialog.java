package com.commonwealthrobotics.kloster;

import com.commonwealthrobotics.ActiveProject;
import com.commonwealthrobotics.controls.SelectionSession;
import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.AddFromFile;
import com.neuronrobotics.sdk.addons.kinematics.math.TransformNR;
import java.awt.geom.Area;
import java.awt.geom.PathIterator;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import javafx.application.Platform;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.*;
import javafx.scene.canvas.Canvas;
import javafx.scene.canvas.GraphicsContext;
import javafx.scene.control.*;
import javafx.scene.input.KeyCode;
import javafx.scene.input.MouseButton;
import javafx.scene.layout.*;
import javafx.scene.paint.Color;
import javafx.scene.paint.PhongMaterial;
import javafx.scene.shape.*;
import javafx.scene.text.Font;
import javafx.scene.transform.Rotate;
import javafx.stage.FileChooser;
import javafx.stage.Modality;
import javafx.stage.Stage;
import javafx.stage.Window;

public final class SketchDialog {
    private static final double SCALE = 3, MARGIN = 30, EDGE = 540;
    private static final Color RED = Color.web("#c62b2f");
    private static final Gson JSON = new GsonBuilder().setPrettyPrinting().create();
    private final ActiveProject ap;
    private final SelectionSession session;
    private final Stage stage = new Stage();
    private final Canvas canvas = new Canvas(600, 600);
    private final Label message = new Label();
    private final Label dimensions = new Label("Tegn din første figur på pladen.");
    private final TextField height = new TextField("3");
    private final TextField width = new TextField("3");
    private final CheckBox cut = new CheckBox("Skær hul");
    private final CheckBox snap = new CheckBox("Fastgør til 1 mm");
    private final Group previewObjects = new Group();
    private final List<SketchGeometry.Mark> redo = new ArrayList<>();
    private final Button insert = new Button("Indsæt i CaDoodle");
    private final Button finish = new Button("Luk polygon");
    private final Button undo = new Button("Fortryd");
    private final Button redoButton = new Button("Gentag");
    private final VBox root = new VBox(12);
    private SketchGeometry.Drawing drawing;
    private SketchGeometry.Mark active;
    private String tool;
    private boolean busy;

    public SketchDialog(Window owner, ActiveProject ap, SelectionSession session,
            boolean freehand, SketchGeometry.Drawing drawing) {
        this.ap = ap; this.session = session; this.drawing = drawing;
        tool = freehand ? "Frihånd" : "Polygon";
        stage.initOwner(owner); stage.initModality(Modality.WINDOW_MODAL);
        stage.setOnCloseRequest(e -> { if (busy) e.consume(); });
        stage.setTitle(freehand ? "Frihånd · Makerværkstedet" : "2D-skitse · Makerværkstedet");
        root.setPadding(new Insets(18)); root.getStyleClass().add("kloster-dialog");
        Label heading = new Label(freehand ? "Frihånd → 3D" : "Fra 2D-skitse til 3D");
        heading.getStyleClass().add("kloster-heading");
        Label intro = new Label(freehand
                ? "Tegn med musen. Vælg stregbredde og giv tegningen højde."
                : "Tegn en lukket figur. Vælg højden, og se den blive til en 3D-form.");
        Button close = new Button("Tilbage til modellen"); close.setOnAction(e -> stage.close());
        Region spacer = new Region(); HBox.setHgrow(spacer, Priority.ALWAYS);
        HBox title = new HBox(14, new VBox(4, heading, intro), spacer, close);
        title.setAlignment(Pos.CENTER_LEFT);

        ToggleGroup modes = new ToggleGroup();
        HBox toolbar = new HBox(7); toolbar.setAlignment(Pos.CENTER_LEFT);
        for (String name : List.of("Polygon", "Rektangel", "Ellipse", "Frihånd", "Viskelæder")) {
            ToggleButton button = new ToggleButton(name); button.setToggleGroup(modes);
            button.setSelected(tool.equals(name));
            button.setOnAction(e -> {
                active = null; tool = name; button.setSelected(true); updateHelp(); paint();
            }); toolbar.getChildren().add(button);
        }
        snap.setSelected(!freehand);
        toolbar.getChildren().addAll(new Separator(), cut, snap);
        cut.setTooltip(new Tooltip("Fjern området fra de figurer, du allerede har tegnet."));

        VBox sidebar = new VBox(12); sidebar.setPrefWidth(310); sidebar.setMinWidth(310);
        Label sizeHeading = new Label("Giv tegningen højde"); sizeHeading.getStyleClass().add("kloster-subheading");
        height.setText(format(drawing.height)); height.setPrefColumnCount(5); height.setMaxWidth(90);
        width.setPrefColumnCount(5); width.setMaxWidth(90);
        HBox sizeRow = new HBox(8, new Label("Højde"), height, new Label("mm")); sizeRow.setAlignment(Pos.CENTER_LEFT);
        HBox strokeRow = new HBox(8, new Label("Stregbredde"), width, new Label("mm")); strokeRow.setAlignment(Pos.CENTER_LEFT);
        Label limits = new Label("Højde: 0,4–180 mm · Streg: 0,4–30 mm"); limits.getStyleClass().add("kloster-muted");
        Button update = new Button("Vis i 3D"); update.setOnAction(e -> updatePreview());
        height.setOnAction(e -> updatePreview());
        height.focusedProperty().addListener((o, was, is) -> { if (!is) updatePreview(); });

        PointLight light = new PointLight(Color.WHITE);
        light.setTranslateX(-180); light.setTranslateY(-200); light.setTranslateZ(-300);
        SubScene preview = new SubScene(new Group(previewObjects, new AmbientLight(Color.web("#777777")), light),
                308, 250, true, SceneAntialiasing.BALANCED);
        preview.setFill(Color.web("#f4f3f0"));
        PerspectiveCamera camera = new PerspectiveCamera(true); camera.setTranslateZ(-430);
        camera.setNearClip(0.1); camera.setFarClip(3000); preview.setCamera(camera);
        previewObjects.getTransforms().addAll(new Rotate(-62, Rotate.X_AXIS), new Rotate(-28, Rotate.Z_AXIS));
        final double[] mouse = new double[2];
        preview.setOnMousePressed(e -> { mouse[0]=e.getX(); mouse[1]=e.getY(); });
        preview.setOnMouseDragged(e -> {
            ((Rotate)previewObjects.getTransforms().get(1)).setAngle(
                    ((Rotate)previewObjects.getTransforms().get(1)).getAngle()+(e.getX()-mouse[0])*0.6);
            mouse[0]=e.getX(); mouse[1]=e.getY();
        });
        dimensions.setWrapText(true); dimensions.setMaxWidth(308);
        Label previewHelp = new Label("Træk i 3D-visningen for at dreje figuren."); previewHelp.getStyleClass().add("kloster-muted");
        Button save = new Button("Gem skitse…"); save.setOnAction(e -> save());
        Button load = new Button("Åbn skitse…"); load.setOnAction(e -> load());
        HBox files = new HBox(8, save, load);
        insert.getStyleClass().add("kloster-primary"); insert.setMaxWidth(Double.MAX_VALUE);
        insert.setOnAction(e -> insert());
        Label plate = new Label("A1 mini · 180 × 180 mm\n0 mm er i pladens nederste venstre hjørne.\nPladen printes ikke. Printværkstedets elevgrænse er 175 × 175 × 175 mm.");
        plate.setWrapText(true); plate.getStyleClass().add("kloster-muted");
        sidebar.getChildren().addAll(sizeHeading,sizeRow,strokeRow,limits,update,preview,dimensions,previewHelp,
                new Separator(),files,insert,plate);

        undo.setOnAction(e -> undo()); redoButton.setOnAction(e -> redo());
        finish.setOnAction(e -> finishPolygon());
        Button clear = new Button("Ryd tegning"); clear.setOnAction(e -> {
            if (drawing.marks.isEmpty()) return;
            Alert confirm = new Alert(Alert.AlertType.CONFIRMATION,
                    "Vil du rydde hele tegningen? Gem skitsen først, hvis du vil bruge den senere.", ButtonType.CANCEL, ButtonType.OK);
            confirm.initOwner(stage); confirm.setHeaderText("Ryd tegning");
            confirm.showAndWait().filter(ButtonType.OK::equals).ifPresent(v -> {
                drawing.marks.clear(); redo.clear(); active=null; paint(); updatePreview();
            });
        });
        HBox edit = new HBox(8, undo, redoButton, finish, clear); edit.setAlignment(Pos.CENTER_LEFT);
        message.setWrapText(true); message.setMinHeight(34); message.setMaxWidth(600);
        VBox drawingBox = new VBox(7, canvas, edit, message);
        HBox body = new HBox(22, drawingBox, sidebar);
        root.getChildren().addAll(title, toolbar, body);
        root.setMaxWidth(1000);
        StackPane shell = new StackPane(root); shell.setAlignment(Pos.TOP_CENTER);
        shell.setStyle("-fx-background-color: white;");
        Scene scene = new Scene(shell, 1000, 828); scene.getStylesheets().add(ActiveProject.getStyleSheetURL());
        stage.setScene(scene); stage.setResizable(false);
        scene.setOnKeyPressed(e -> {
            if (e.getTarget() instanceof TextInputControl) return;
            if (e.getCode() == KeyCode.ESCAPE && active != null) { active=null; paint(); e.consume(); }
            if (e.getCode() == KeyCode.ENTER && active != null && tool.equals("Polygon")) { finishPolygon(); e.consume(); }
            if (e.isControlDown() && e.getCode() == KeyCode.Z) { if(e.isShiftDown()) redo(); else undo(); e.consume(); }
            if (e.isControlDown() && e.getCode() == KeyCode.Y) { redo(); e.consume(); }
        });
        canvas.setOnMousePressed(e -> {
            if (busy || e.getButton() != MouseButton.PRIMARY || !onPlate(e.getX(),e.getY())) return;
            canvas.requestFocus();
            try {
                double[] p = point(e.getX(),e.getY());
                if (tool.equals("Polygon")) {
                    if (active == null) active = newMark();
                    if (active.points.size() >= 3 && distance(active.points.getFirst(),p) < 2) finishPolygon();
                    else {
                        if (active.points.isEmpty() || distance(active.points.getLast(),p) > 0.1) active.points.add(p);
                        if (e.getClickCount() == 2) finishPolygon();
                    }
                } else { active = newMark(); active.points.add(p); }
                paint();
            } catch (IllegalArgumentException ex) { error(ex); }
        });
        canvas.setOnMouseDragged(e -> {
            if (busy || active == null || tool.equals("Polygon")) return;
            double[] p = point(e.getX(),e.getY());
            if (tool.equals("Rektangel") || tool.equals("Ellipse")) {
                if(active.points.size()>1) active.points.removeLast(); active.points.add(p);
            } else if (distance(active.points.getLast(),p)>0.35 && active.points.size()<10000) active.points.add(p);
            paint();
        });
        canvas.setOnMouseReleased(e -> {
            if (active != null && !tool.equals("Polygon")) commit();
        });
        updateHelp(); paint(); updatePreview();
    }

    public void show() { stage.show(); stage.centerOnScreen(); }
    private boolean onPlate(double x,double y) {return x>=MARGIN&&x<=MARGIN+EDGE&&y>=MARGIN&&y<=MARGIN+EDGE;}
    private double[] point(double x,double y) {
        x=Math.max(0,Math.min(180,(x-MARGIN)/SCALE)); y=Math.max(0,Math.min(180,(y-MARGIN)/SCALE));
        if(snap.isSelected()){x=Math.rint(x); y=Math.rint(y);} return new double[]{x,y};
    }
    private double distance(double[] a,double[] b){return Math.hypot(a[0]-b[0],a[1]-b[1]);}
    private double number(TextField field,double min,double max,String name) {
        try {double n=Double.parseDouble(field.getText().strip().replace(',','.'));
            if(Double.isFinite(n)&&n>=min&&n<=max)return n;
        }catch(NumberFormatException ignored){}
        throw new IllegalArgumentException(name+" skal være mellem "+format(min)+" og "+format(max)+" mm.");
    }
    private SketchGeometry.Mark newMark(){return new SketchGeometry.Mark(tool,cut.isSelected()||tool.equals("Viskelæder"),number(width,0.4,30,"Stregbredden"));}
    private void finishPolygon(){
        if(active==null||active.points.size()<3){message.setText("Klik mindst tre forskellige punkter, før du lukker polygonen.");return;}
        commit();
    }
    private void commit(){
        if(active!=null&&!new Area(SketchGeometry.shape(active)).isEmpty()){drawing.marks.add(active);redo.clear();}
        active=null; paint(); updateHelp(); updatePreview();
    }
    private void undo(){
        if(active!=null){active=null;}else if(!drawing.marks.isEmpty())redo.add(drawing.marks.removeLast());
        paint(); updatePreview();
    }
    private void redo(){if(!redo.isEmpty()){drawing.marks.add(redo.removeLast());paint();updatePreview();}}
    private void updateHelp(){
        message.setText(switch(tool){
            case "Polygon" -> "Klik hjørnerne. Klik første punkt igen, dobbeltklik eller vælg ‘Luk polygon’. Esc afbryder.";
            case "Rektangel", "Ellipse" -> "Træk fra det ene hjørne til det modsatte. Markér ‘Skær hul’ for at fjerne området.";
            case "Viskelæder" -> "Træk hen over tegningen for at viske ud. Stregbredden bestemmer viskelæderets størrelse.";
            default -> "Hold museknappen nede, og tegn frit. Overlappende streger bliver samlet til én form.";
        }); message.setTextFill(Color.web("#666666")); finish.setDisable(!tool.equals("Polygon"));
    }
    private void paint(){
        GraphicsContext g=canvas.getGraphicsContext2D();
        g.setFill(Color.WHITE);g.fillRect(0,0,600,600);
        g.setFont(Font.font("Metrophobic",11));
        for(int i=0;i<=180;i+=10){
            double p=MARGIN+i*SCALE;g.setStroke(Color.web(i%30==0?"#cccccc":"#e8e8e8"));g.setLineWidth(1);
            g.strokeLine(p,MARGIN,p,MARGIN+EDGE);g.strokeLine(MARGIN,p,MARGIN+EDGE,p);
            if(i%30==0){g.setFill(Color.web("#666666"));g.fillText(""+i,p-8,MARGIN+EDGE+18);g.fillText(""+(180-i),3,p+4);}
        }
        g.setStroke(RED);g.setLineWidth(1.5);g.strokeRect(MARGIN,MARGIN,EDGE,EDGE);
        g.setFill(Color.web("#333333"));g.fillText("A1 mini · 180 × 180 mm",MARGIN,17);
        List<SketchGeometry.Mark> marks=new ArrayList<>(drawing.marks);
        if(active!=null)marks.add(active);
        g.save();g.translate(MARGIN,MARGIN);g.scale(SCALE,SCALE);g.setFill(RED);g.setGlobalAlpha(0.85);
        g.setFillRule(FillRule.EVEN_ODD);g.beginPath();
        double[] p=new double[6];
        for(PathIterator it=SketchGeometry.area(marks).getPathIterator(null,0.12);!it.isDone();it.next()){
            int type=it.currentSegment(p);
            if(type==PathIterator.SEG_MOVETO)g.moveTo(p[0],p[1]);
            if(type==PathIterator.SEG_LINETO)g.lineTo(p[0],p[1]);
            if(type==PathIterator.SEG_CLOSE)g.closePath();
        }g.fill();g.setGlobalAlpha(1);
        if(active!=null&&tool.equals("Polygon")){
            g.setStroke(Color.web("#333333"));g.setLineWidth(0.5);g.beginPath();
            for(int i=0;i<active.points.size();i++){double[] q=active.points.get(i);if(i==0)g.moveTo(q[0],q[1]);else g.lineTo(q[0],q[1]);}g.stroke();
            g.setFill(Color.web("#333333"));for(double[] q:active.points)g.fillOval(q[0]-1,q[1]-1,2,2);
        }g.restore();undo.setDisable(active==null&&drawing.marks.isEmpty());redoButton.setDisable(redo.isEmpty());
    }

    private void updatePreview(){
        try{
            drawing.height=number(height,0.4,180,"Højden");
            Area area=SketchGeometry.area(drawing.marks);previewObjects.getChildren().clear();
            insert.setDisable(area.isEmpty()||busy);
            if(area.isEmpty()){dimensions.setText("Tegn din første figur på pladen.");return;}
            var mesh=SketchGeometry.extrude(area,drawing.height);var bounds=area.getBounds2D();
            TriangleMesh fx=new TriangleMesh();fx.getTexCoords().addAll(0,0);
            int index=0;
            for(var tri:mesh){
                for(double[] p:List.of(tri.a(),tri.b(),tri.c()))fx.getPoints().addAll((float)(p[0]-bounds.getCenterX()),(float)(p[1]-(180-bounds.getCenterY())),(float)-p[2]);
                fx.getFaces().addAll(index,0,index+2,0,index+1,0);index+=3;
            }
            MeshView view=new MeshView(fx);view.setCullFace(CullFace.NONE);view.setMaterial(new PhongMaterial(RED));
            double scale=150/Math.max(30,Math.max(bounds.getWidth(),Math.max(bounds.getHeight(),drawing.height)));
            view.setScaleX(scale);view.setScaleY(scale);view.setScaleZ(scale);previewObjects.getChildren().add(view);
            dimensions.setText(format(bounds.getWidth())+" × "+format(bounds.getHeight())+" × "+format(drawing.height)+" mm\nBredde × dybde × højde");
        }catch(Exception ex){insert.setDisable(true);error(ex);}
    }
    private static String format(double n){return String.format(java.util.Locale.forLanguageTag("da"),"%.1f",n);}
    private void error(Exception ex){message.setText(ex.getMessage()==null?"Figuren kunne ikke beregnes. Prøv at forenkle tegningen.":ex.getMessage());message.setTextFill(RED);}
    private Path folder() throws java.io.IOException{
        Path p=Path.of(System.getProperty("user.home"),"Projekter","3D-print","Skitser");Files.createDirectories(p);return p;
    }
    private FileChooser chooser() throws java.io.IOException{
        FileChooser fc=new FileChooser();fc.setInitialDirectory(folder().toFile());
        fc.getExtensionFilters().add(new FileChooser.ExtensionFilter("Kloster-skitse (*.klosterskitse)","*.klosterskitse"));return fc;
    }
    private void save(){try{
        if(active!=null)throw new IllegalArgumentException("Afslut figuren, før du gemmer skitsen.");
        drawing.height=number(height,0.4,180,"Højden");var fc=chooser();fc.setInitialFileName("Min-skitse.klosterskitse");
        var file=fc.showSaveDialog(stage);if(file!=null)Files.writeString(file.toPath(),JSON.toJson(drawing));
    }catch(Exception ex){error(ex);}}
    private void load(){try{
        var file=chooser().showOpenDialog(stage);if(file==null)return;
        if(Files.size(file.toPath())>5_000_000)throw new IllegalArgumentException("Skitsefilen er for stor.");
        var loaded=JSON.fromJson(Files.readString(file.toPath()),SketchGeometry.Drawing.class);SketchGeometry.validate(loaded);
        drawing.marks.clear();drawing.marks.addAll(loaded.marks);drawing.height=loaded.height;
        height.setText(format(drawing.height));active=null;redo.clear();paint();updatePreview();
    }catch(Exception ex){error(ex);}}
    private void insert(){
        if(busy)return;
        try{
            if(active!=null)throw new IllegalArgumentException("Afslut figuren, før du indsætter den.");
            if(ap.get().isOperationRunning()||ap.get().getPercentInitialized()<0.99)
                throw new IllegalArgumentException("CaDoodle arbejder stadig. Vent et øjeblik, og prøv igen.");
            drawing.height=number(height,0.4,180,"Højden");SketchGeometry.validate(drawing);
            var mesh=SketchGeometry.extrude(SketchGeometry.area(drawing.marks),drawing.height);
            String name="Skitse-"+java.time.LocalDateTime.now().format(java.time.format.DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss-SSS"));
            Path stl=folder().resolve(name+".stl");SketchGeometry.writeStl(stl,mesh);
            Files.writeString(folder().resolve(name+".klosterskitse"),JSON.toJson(drawing));
            busy=true;root.setDisable(true);message.setText("Indsætter din figur…");
            // Capture the destination before leaving the JavaFX thread.
            var project=ap.get();
            Thread worker=new Thread(()->{
                try{
                    AddFromFile operation=new AddFromFile().set(stl.toFile(),project).setLocation(new TransformNR(-90,-90,0));
                    if(ap.get()!=project)throw new IllegalStateException("Det aktive projekt blev ændret. Åbn skitsen igen i det ønskede projekt.");
                    ap.addOp(operation).join();
                    var names=operation.getNamesAddedInThisOperation();
                    if(names==null||names.isEmpty())throw new IllegalStateException("CaDoodle kunne ikke indsætte figuren. Skitsen er gemt i mappen Skitser.");
                    Platform.runLater(()->{session.selectAllFromCurrentState(names);session.setKeyBindingFocus();stage.close();});
                }catch(Exception ex){Platform.runLater(()->{busy=false;root.setDisable(false);error(ex);});}
            },"Kloster-skitse-import");worker.setDaemon(true);worker.start();
        }catch(Exception ex){error(ex);}
    }
}
