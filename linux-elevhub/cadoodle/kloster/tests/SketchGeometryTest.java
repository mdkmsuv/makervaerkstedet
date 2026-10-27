import com.commonwealthrobotics.kloster.SketchGeometry;
import java.awt.geom.Area;
import java.awt.geom.Rectangle2D;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/** Checks manufacturing invariants, not UI implementation details. */
public class SketchGeometryTest {
    private static final Path DEFAULT = Path.of("fixtures");
    public static void main(String[] args) throws Exception {
        Path folder=args.length>0?Path.of(args[0]):DEFAULT;Files.createDirectories(folder);
        check("rectangle-20x30x3",new Area(new Rectangle2D.Double(10,10,20,30)),3,1800,folder);
        Area hole=new Area(new Rectangle2D.Double(0,0,40,40));hole.subtract(new Area(new Rectangle2D.Double(10,10,20,20)));
        check("through-hole",hole,5,6000,folder);
        hole.add(new Area(new Rectangle2D.Double(15,15,10,10)));
        check("island-in-hole",hole,5,6500,folder);
        Area concave=new Area(new Rectangle2D.Double(0,0,20,10));concave.add(new Area(new Rectangle2D.Double(0,0,10,30)));
        check("concave-L",concave,4,1600,folder);
        Area disjoint=new Area(new Rectangle2D.Double(0,0,10,10));disjoint.add(new Area(new Rectangle2D.Double(20,20,10,10)));
        check("two-solids",disjoint,2,400,folder);
        var rect=mark("Rektangel",false,3,new double[]{0,0},new double[]{30,20});
        var rect2=mark("Rektangel",false,3,new double[]{20,0},new double[]{40,20});
        check("overlap-unioned",SketchGeometry.area(List.of(rect,rect2)),3,2400,folder);
        var scribble=mark("Frihånd",false,4,new double[]{20,20},new double[]{45,45},new double[]{20,45},new double[]{45,20});
        check("crossing-scribble",SketchGeometry.area(List.of(scribble)),3,-1,folder);
        var eraser=mark("Viskelæder",true,5,new double[]{10,30},new double[]{55,30});
        check("erased-scribble",SketchGeometry.area(List.of(scribble,eraser)),3,-1,folder);
        var circle=mark("Ellipse",false,3,new double[]{20,20},new double[]{40,40});
        check("circle-20mm",SketchGeometry.area(List.of(circle)),3,Math.PI*100*3,folder);
        var edge=mark("Frihånd",false,8,new double[]{0,0},new double[]{180,0},new double[]{180,180});
        var edgeMesh=check("plate-edge-clipped",SketchGeometry.area(List.of(edge)),2,-1,folder);
        for(var t:edgeMesh)for(var p:List.of(t.a(),t.b(),t.c()))if(p[0]<0||p[0]>180||p[1]<0||p[1]>180)throw new AssertionError("Outside plate");
        for(double invalid:new double[]{0,Double.NaN,181}){
            try{SketchGeometry.extrude(new Area(new Rectangle2D.Double(0,0,10,10)),invalid);throw new AssertionError("Invalid height accepted");}
            catch(IllegalArgumentException expected){}
        }
        try{SketchGeometry.extrude(new Area(),3);throw new AssertionError("Empty geometry accepted");}catch(IllegalArgumentException expected){}
        var drawing=new SketchGeometry.Drawing();drawing.marks.add(scribble);drawing.marks.add(eraser);drawing.height=4.5;
        var gson=new com.google.gson.Gson();
        var restored=gson.fromJson(gson.toJson(drawing),SketchGeometry.Drawing.class);
        SketchGeometry.validate(restored);
        if(restored.height!=4.5||!SketchGeometry.area(restored.marks).equals(SketchGeometry.area(drawing.marks)))
            throw new AssertionError("Editable drawing changed after save/load");
        restored.marks.getFirst().points.getFirst()[0]=Double.NaN;
        try{SketchGeometry.validate(restored);throw new AssertionError("Invalid file accepted");}catch(IllegalArgumentException expected){}
        System.out.println("PASS: 10 closed, oriented STL fixtures; holes, concavity, unions, erasing, clipping and input bounds.");
    }
    static SketchGeometry.Mark mark(String tool,boolean cut,double width,double[]... points){
        var mark=new SketchGeometry.Mark(tool,cut,width);mark.points.addAll(List.of(points));return mark;
    }
    static List<SketchGeometry.Triangle> check(String name,Area area,double height,double expected,Path folder)throws Exception{
        var mesh=SketchGeometry.extrude(area,height);Map<String,Integer> count=new HashMap<>(),balance=new HashMap<>();double volume=0;
        for(var t:mesh){
            double[] a=t.a(),b=t.b(),c=t.c();
            volume+=(a[0]*(b[1]*c[2]-b[2]*c[1])-a[1]*(b[0]*c[2]-b[2]*c[0])+a[2]*(b[0]*c[1]-b[1]*c[0]))/6;
            List<double[]> p=List.of(a,b,c);
            for(int i=0;i<3;i++){
                String u=key(p.get(i)),v=key(p.get((i+1)%3));boolean forward=u.compareTo(v)<0;String edge=forward?u+"|"+v:v+"|"+u;
                if(u.equals(v))throw new AssertionError(name+": zero length edge");
                count.merge(edge,1,Integer::sum);balance.merge(edge,forward?1:-1,Integer::sum);
            }
        }
        for(String edge:count.keySet())if(count.get(edge)!=2||balance.get(edge)!=0)throw new AssertionError(name+": non-manifold edge "+edge+" count "+count.get(edge));
        if(volume<=0)throw new AssertionError(name+": inverted or empty volume");
        if(expected>0&&Math.abs(volume-expected)>expected*0.01)throw new AssertionError(name+": volume "+volume+" expected "+expected);
        SketchGeometry.writeStl(folder.resolve(name+".stl"),mesh);
        if(Files.size(folder.resolve(name+".stl"))!=84+50L*mesh.size())throw new AssertionError("Invalid binary STL length");
        System.out.println(name+": "+mesh.size()+" triangles, "+Math.round(volume)+" mm3, closed and oriented");return mesh;
    }
    static String key(double[] p){return Float.floatToIntBits((float)(p[0]+0.0))+","+Float.floatToIntBits((float)(p[1]+0.0))+","+Float.floatToIntBits((float)(p[2]+0.0));}
}
