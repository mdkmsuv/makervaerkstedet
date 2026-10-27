import com.neuronrobotics.bowlerstudio.scripting.cadoodle.*;
import com.neuronrobotics.sdk.addons.kinematics.math.TransformNR;
import eu.mihosoft.vrl.v3d.CSG;
import eu.mihosoft.vrl.v3d.Cube;
import eu.mihosoft.vrl.v3d.parametrics.LengthParameter;
import java.nio.file.*;
import java.util.*;

/** Real release-JAR geometry and persisted timeline audit; no scene/input or replacement operations. */
public final class AdvancedGeometryHistoryProbe {
    static final double EPS=1e-4;
    public static void main(String[] args) throws Exception {
        Path root=Path.of(args[0]).toAbsolutePath(); Files.createDirectories(root);
        CaDoodleFile.setImageEngine((db, shapes, file)->new javafx.scene.image.WritableImage(1,1));
        CSG.setDefaultOptType(CSG.OptType.Manifold3d);
        require(CSG.getManifold()!=null,"native Manifold3d backend unavailable; geometry audit cannot pass");
        List<Throwable> failures=new ArrayList<>();
        for(String kind: List.of("fillet-default","chamfer-changed","extrude-default","extrude-inset","sweep-default","sweep-changed")) {
            try { run(root,kind); } catch(Throwable t) { failures.add(t); System.err.println("FAIL: "+kind); t.printStackTrace(); }
        }
        require(failures.isEmpty(), failures.size()+" geometry/history case(s) failed");
        System.out.println("PASS: all six native geometry cases and save/reopen/undo/redo");
    }
    static void run(Path root,String kind) throws Exception {
        Path dir=root.resolve(kind); Files.createDirectories(dir);
        Path path=dir.resolve("project.doodle");
        CaDoodleFile p=new CaDoodleFile().setSelf(path.toFile()).setProjectName(kind); p.initialize();
        try {
            Path sources=dir.resolve("sources"); Files.createDirectories(sources);
            String target=seed(p,sources.resolve("target.stl"),new Cube(12,8,4).toCSG());
            String other=seed(p,sources.resolve("other.stl"),new Cube(3,5,7).toCSG().movex(60));
            Map<String,State> before=snapshot(p);
            System.out.println("FIXTURE: "+kind+" "+before.get(target));
            AbstractAddFrom operation;
            if(kind.startsWith("fillet") || kind.startsWith("chamfer")) {
                FilletChamfer f=new FilletChamfer().setToFillet(Set.of(target)).setWorkplane(new TransformNR());
                f.setCaDoodleFile(p);
                if(kind.startsWith("chamfer")) {
                    // Names verified from the pinned JAR's BootstrapMethods. One face is the native chamfer.
                    new LengthParameter(f.getDb(),f.getName()+"_CaDoodle_Num Faces",1.0,new ArrayList<>()).setMM(1);
                    new LengthParameter(f.getDb(),f.getName()+"_CaDoodle_Fillet Size",1.0,new ArrayList<>()).setMM(1);
                }
                operation=f;
            } else {
                boolean sweep=kind.startsWith("sweep");
                CSG source=CaDoodleFile.getByName(p.getCurrentState(),target);
                ExtrudeSurface e=new ExtrudeSurface().setToExtrude(Set.of(target))
                        .setSlicePlane(new TransformNR(source.getCenterX(),source.getCenterY(),source.getMaxZ())).setSweep(sweep);
                e.setCaDoodleFile(p);
                if(kind.equals("extrude-inset")) e.inset(e.getName()).setMM(1);
                if(kind.equals("sweep-changed")) { e.setDefrad(14); e.setDefstep(16); e.setDefangle(45); e.setDefz(3); }
                operation=e;
            }
            join(p.addOperation(operation));
            Map<String,State> after=snapshot(p);
            require(after.size()>before.size(),kind+": operation added no geometry");
            same(before.get(target),after.get(target),kind+": source changed");
            same(before.get(other),after.get(other),kind+": unrelated object changed");
            List<CSG> added=p.getCurrentState().stream().filter(c->!before.containsKey(c.getName())).toList();
            require(!added.isEmpty(),kind+": generated geometry missing");
            StringBuilder metrics=new StringBuilder();
            for(CSG c:added) {
                State s=new State(c);
                require(s.volume>EPS&&Double.isFinite(s.volume),kind+": empty/nonfinite volume");
                require(s.sx>EPS&&s.sy>EPS&&s.sz>EPS,kind+": degenerate bounds");
                if(kind.equals("extrude-default")) {
                    require(close(s.sx,12)&&close(s.sy,8)&&close(s.sz,20),"default extrusion must be 12x8x20: "+s);
                    require(close(s.volume,1920),"default extrusion volume must equal 1920: "+s.volume);
                }
                // Slice.slice(CSG, offset) offsets the outline outward for positive values.
                if(kind.equals("extrude-inset")) require(close(s.sx,14)&&close(s.sy,10)&&close(s.sz,20)
                        &&s.volume>2700&&s.volume<2800,"1 mm outline offset must produce 14x10x20 rounded extrusion: "+s);
                if(kind.equals("fillet-default")) require(s.hole&&s.sz>1.9&&s.sz<2.1&&s.volume>20&&s.volume<50,
                        "default 2 mm inner fillet must create a shallow cutting shape: "+s);
                if(kind.equals("chamfer-changed")) require(s.hole&&s.sz>.9&&s.sz<1.1&&s.volume>10&&s.volume<25,
                        "1 mm one-face chamfer must create a smaller cutting shape: "+s);
                if(kind.equals("sweep-default")) require(close(s.sx,22)&&close(s.sy,8)&&close(s.sz,22)
                        &&s.volume>2300&&s.volume<2500,"default quarter-turn sweep bounds/volume differ: "+s);
                if(kind.equals("sweep-changed")) require(s.sx>15&&s.sx<17&&s.sy>8&&s.sy<9&&s.sz>18&&s.sz<19
                        &&s.volume>1400&&s.volume<1600,"45 degree radius14 z3 step16 sweep bounds/volume differ: "+s);
                metrics.append(c.getName()).append(" ").append(s).append('\n');
            }
            Files.writeString(dir.resolve("metrics.txt"),metrics);
            p.save(true);
            try(CloseProject reopened=new CloseProject(CaDoodleFile.fromFile(path.toFile()))) {
                CaDoodleFile r=reopened.p;
                await(after,r,"reopen "+kind);
                require(r.isBackAvailable(),"reopened undo unavailable"); r.back(); await(before,r,"reopened undo "+kind);
                require(r.isForwardAvailable(),"reopened redo unavailable"); r.forward(); await(after,r,"reopened redo "+kind);
                require(!r.isForwardAvailable(),"redo did not reach final state");
            }
            System.out.println("PASS: "+kind+" generated="+added.size()+" save/reopen/undo/redo");
        } finally { p.close(); }
    }
    static String seed(CaDoodleFile p,Path file,CSG c) throws Exception {
        Files.writeString(file,c.toStlString());
        AddFromFile op=new AddFromFile().set(file.toFile(),p).setPreventBoM(true); join(p.addOperation(op));
        require(op.getNamesAddedInThisOperation().size()==1,"seed import failed");
        return op.getNamesAddedInThisOperation().get(0);
    }
    static void join(Thread t) throws Exception { if(t!=null){t.join(60000);require(!t.isAlive(),"operation timeout");} }
    static Map<String,State> snapshot(CaDoodleFile p) { Map<String,State> m=new TreeMap<>();for(CSG c:p.getCurrentState())m.put(c.getName(),new State(c));return m; }
    static void await(Map<String,State> expected,CaDoodleFile p,String context) throws Exception {
        AssertionError last=null; long end=System.nanoTime()+30_000_000_000L;
        while(System.nanoTime()<end) {try {Map<String,State> actual=snapshot(p);require(expected.keySet().equals(actual.keySet()),context+": names differ");for(String n:expected.keySet())same(expected.get(n),actual.get(n),context+" "+n);return;}catch(AssertionError e){last=e;Thread.sleep(25);}}
        throw last;
    }
    static void same(State a,State b,String c) {require(a!=null&&b!=null&&a.same(b),c+" expected="+a+" actual="+b);}
    static boolean close(double a,double b){return Math.abs(a-b)<EPS;}
    static void require(boolean ok,String m){if(!ok)throw new AssertionError(m);}
    static final class CloseProject implements AutoCloseable {final CaDoodleFile p;CloseProject(CaDoodleFile p){this.p=p;}public void close(){p.close();}}
    static final class State {
        final double x,y,z,sx,sy,sz,volume; final boolean hole,group,hidden; final int vertices; final List<String> coordinates;
        State(CSG c){x=c.getCenterX();y=c.getCenterY();z=c.getCenterZ();sx=c.getTotalX();sy=c.getTotalY();sz=c.getTotalZ();hole=c.isHole();group=c.isInGroup();hidden=c.isHide();vertices=c.getVertices().length;volume=volume(c);
            coordinates=new ArrayList<>();double[] v=c.getVertices();for(int i=0;i<v.length;i+=3){require(Double.isFinite(v[i])&&Double.isFinite(v[i+1])&&Double.isFinite(v[i+2]),"nonfinite vertex");coordinates.add(Math.round(v[i]/EPS)+","+Math.round(v[i+1]/EPS)+","+Math.round(v[i+2]/EPS));}Collections.sort(coordinates);}
        boolean same(State b){return close(x,b.x)&&close(y,b.y)&&close(z,b.z)&&close(sx,b.sx)&&close(sy,b.sy)&&close(sz,b.sz)&&close(volume,b.volume)&&vertices==b.vertices&&coordinates.equals(b.coordinates)&&hole==b.hole&&group==b.group&&hidden==b.hidden;}
        public String toString(){return "center="+x+","+y+","+z+" size="+sx+"x"+sy+"x"+sz+" volume="+volume+" vertexCoordinates="+vertices+" hole="+hole;}
    }
    static double volume(CSG c) {
        double total=0; for(var p:c.getPolygons()) {var v=p.getVertices(); if(v.size()<3)continue;var a=v.get(0).pos;
            for(int i=1;i<v.size()-1;i++){var b=v.get(i).pos;var d=v.get(i+1).pos;total+=(a.x*(b.y*d.z-b.z*d.y)+a.y*(b.z*d.x-b.x*d.z)+a.z*(b.x*d.y-b.y*d.x))/6;}}
        return Math.abs(total);
    }
}
