package com.commonwealthrobotics.kloster;

import com.commonwealthrobotics.ActiveProject;
import com.commonwealthrobotics.controls.SelectionSession;
import com.google.gson.*;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.AddFromFile;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.CaDoodleFile;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.SaveOverwriteException;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.HexFormat;
import javafx.application.Platform;
import javafx.scene.layout.Pane;
import javafx.scene.control.TextField;
import javafx.stage.Stage;

/** Local file handoff approved by the pupil toolbar. No network or commands. */
public final class ProjectInbox {
    private static final Gson JSON = new Gson();
    private final Path root = Path.of(System.getProperty("user.home"),"Projekter",".elevhub","open-requests");
    public ProjectInbox(ActiveProject ap, SelectionSession session, Pane owner) {
        Thread watcher = new Thread(()->{
            while(true) {
                try {
                    if(Files.isDirectory(root)) try(var files=Files.list(root)) {
                        for(Path request:files.filter(p->p.getFileName().toString().matches("[a-f0-9]{32}\\.ready")).toList()) {
                            if(!ap.isOpen()||ap.isPreparingProjectFinish()||!ap.get().isInitialized()||ap.get().isOperationRunning()) continue;
                            open(request,ap,session,owner);
                        }
                    }
                    Thread.sleep(750);
                } catch(InterruptedException ex) {return;} catch(Exception ex) {System.err.println("Project inbox: "+ex.getClass().getSimpleName());}
            }
        },"Elevhub-project-inbox");
        watcher.setDaemon(true);watcher.start();
    }
    private void status(Path request,String state,String message)throws Exception {
        String id=request.getFileName().toString().replace(".ready","");
        JsonObject result=new JsonObject();result.addProperty("state",state);result.addProperty("message",message);
        Path temp=root.resolve(id+".status.tmp"),target=root.resolve(id+".status.json");
        Files.writeString(temp,JSON.toJson(result));Files.move(temp,target,StandardCopyOption.REPLACE_EXISTING);
    }
    private static void onFx(Runnable action)throws Exception {
        var completed=new java.util.concurrent.CountDownLatch(1);
        var failure=new java.util.concurrent.atomic.AtomicReference<Throwable>();
        Platform.runLater(()->{try{action.run();}catch(Throwable ex){failure.set(ex);}finally{completed.countDown();}});
        if(!completed.await(30,java.util.concurrent.TimeUnit.SECONDS))throw new Exception("3D-værkstedet svarer ikke. Prøv at åbne projektet igen.");
        if(failure.get()!=null)throw new Exception("Projektet kunne ikke vises i CaDoodle.",failure.get());
    }
    private static void showProject(Pane owner,ActiveProject ap) {
        if(owner.lookup("#fileNameBox") instanceof TextField title)title.setText(ap.get().getMyProjectName());
        Stage window=(Stage)owner.getScene().getWindow();window.show();window.toFront();window.requestFocus();
    }
    private static void saveProject(CaDoodleFile project)throws Exception {
        long until=System.currentTimeMillis()+10000;
        while(true) {
            try{project.save(true);return;}
            catch(SaveOverwriteException busy){
                if(System.currentTimeMillis()>=until)throw new Exception("Projektet gemmes stadig. Prøv igen om lidt.");
                Thread.sleep(150);
            }
        }
    }
    private static void finishBeforeOpening(Pane owner) throws Exception {
        var result = new java.util.concurrent.CompletableFuture<String>();
        ProjectCloseGuard.requestFinish(owner, result::complete);
        if (!"completed".equals(result.get(10, java.util.concurrent.TimeUnit.MINUTES)))
            throw new Exception("Åbningen blev annulleret. Din tegning er stadig åben.");
    }
    private void open(Path request,ActiveProject ap,SelectionSession session,Pane owner) {
        try {
            if(Files.size(request)>4096)throw new Exception("Ugyldig projektåbning.");
            JsonObject data=JSON.fromJson(Files.readString(request),JsonObject.class);
            String id=request.getFileName().toString().replace(".ready","");
            String filename=data.get("file").getAsString();
            if(!id.equals(data.get("id").getAsString())||!filename.matches(id+"\\.(stl|zip|klosterskitse)"))throw new Exception("Ugyldig projektfil.");
            double age=System.currentTimeMillis()/1000.0-data.get("created").getAsDouble();
            if(age<0||age>120)throw new Exception("Åbningen udløb. Åbn projektet igen fra Dine projekter.");
            Path file=root.resolve(filename);
            if(Files.isSymbolicLink(file)||Files.size(file)>50*1024*1024)throw new Exception("Projektfilen er for stor.");
            String digest=HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(file)));
            if(!digest.equals(data.get("sha256").getAsString()))throw new Exception("Projektfilen blev ændret. Åbn den igen.");
            String name=data.get("name").getAsString();
            if(!filename.endsWith(".klosterskitse")) finishBeforeOpening(owner);
            synchronized(ap) {
            CaDoodleFile previousFile=ap.get();
            saveProject(ap.get()); // preserve the current project before switching
            if(filename.endsWith(".klosterskitse")) {
                SketchGeometry.Drawing drawing=JSON.fromJson(Files.readString(file),SketchGeometry.Drawing.class);
                SketchGeometry.validate(drawing);
                onFx(()->{showProject(owner,ap);new SketchDialog(owner.getScene().getWindow(),ap,session,false,drawing).show();});
            } else if(filename.endsWith(".zip")) {
                var previous=ap.get();
                ap.loadFromZip(file.toFile());
                if(ap.get()==previous)throw new Exception("3D-projektet kunne ikke åbnes.");
                long until=System.currentTimeMillis()+60000;
                while(!ap.get().isInitialized()&&System.currentTimeMillis()<until)Thread.sleep(100);
                if(!ap.get().isInitialized())throw new Exception("Projektet blev ikke færdigt med at åbne.");
            } else {
                ap.newProject();
                var project=ap.get();project.setProjectName(name);
                // newProject only creates the file. Match CaDoodle's New action:
                // initialization enables autosave and subsequent inbox requests.
                project.initialize();
                if(!project.isInitialized())throw new Exception("3D-projektet kunne ikke gøres klar.");
                AddFromFile operation=new AddFromFile().set(file.toFile(),project);
                Thread operationThread=ap.addOp(operation);if(operationThread!=null)operationThread.join();
                if(operation.getNamesAddedInThisOperation()==null||operation.getNamesAddedInThisOperation().isEmpty())throw new Exception("STL-filen indeholder ingen model, som CaDoodle kan åbne.");
                saveProject(project);
                Platform.runLater(()->session.selectAllFromCurrentState(operation.getNamesAddedInThisOperation()));
            }
            if(!filename.endsWith(".klosterskitse"))onFx(()->{ProjectStartDialog.openedExternally(owner);showProject(owner,ap);});
            ProjectNaming.cleanupFinishedWorkingCopy(previousFile,ap.get());
            }
            status(request,"done","Åbnet i CaDoodle");
        } catch(Exception ex) {
            try{status(request,"error",ex.getMessage()==null?"Projektet kunne ikke åbnes i CaDoodle.":ex.getMessage());}catch(Exception ignored){}
        } finally {
            try {
                String id=request.getFileName().toString().replace(".ready","");
                for(String extension:new String[]{".stl",".zip",".klosterskitse"})Files.deleteIfExists(root.resolve(id+extension));
                Files.deleteIfExists(request);
            }catch(Exception ignored){}
        }
    }
}
