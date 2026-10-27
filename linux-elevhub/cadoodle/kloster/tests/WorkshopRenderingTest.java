import com.neuronrobotics.bowlerstudio.threed.BowlerStudio3dEngine;
import javafx.scene.Group;
import javafx.scene.Node;
import javafx.scene.paint.PhongMaterial;
import javafx.scene.paint.Color;
import javafx.scene.shape.MeshView;
import javafx.scene.shape.TriangleMesh;
import javafx.scene.transform.Transform;

/**
 * Headless structural regression checks for the physical 180 mm workplane.
 * This deliberately does not create a JavaFX Stage or start the application.
 */
public final class WorkshopRenderingTest {
    private static final double EPS = 1e-6;

    public static void main(String[] args) throws Exception {
        BowlerStudio3dEngine.setLightGrid(Color.web("#eff7fb"));
        BowlerStudio3dEngine.GridHolder grid =
                BowlerStudio3dEngine.createTexturedWorkplane(180, 180);
        require(Math.abs(grid.xSizeMM - 180) < EPS && Math.abs(grid.ySizeMM - 180) < EPS,
                "workplane must remain 180 x 180 mm");

        Group majorMinor = group(grid, "bigGridView");
        Group outline = group(grid, "outlineView");
        Group background = group(grid, "backgroundView");
        require(majorMinor.getChildren().size() == 2,
                "grid must contain separate major and minor meshes");
        require(outline.getChildren().size() == 1 && background.getChildren().size() == 1,
                "outline and background must each contain one mesh");

        MeshView major = mesh(majorMinor.getChildren().get(0));
        MeshView minor = mesh(majorMinor.getChildren().get(1));
        require(major.isMouseTransparent() && minor.isMouseTransparent(),
                "grid meshes must not intercept model picking");
        require(grid.intersectionNode == background.getChildren().get(0),
                "intersection node must be the background pick surface");
        require(!grid.intersectionNode.isMouseTransparent()
                        && Math.abs(grid.intersectionNode.getTranslateZ()) < EPS,
                "workplane pick surface must remain pickable at z=0");
        require(outline.getChildren().get(0).isMouseTransparent(),
                "outline must not intercept model picking");
        grid.setMouseTransparent(true);
        require(grid.intersectionNode.isMouseTransparent(),
                "GridHolder.setMouseTransparent(true) must disable picking");
        grid.setMouseTransparent(false);
        require(!grid.intersectionNode.isMouseTransparent(),
                "GridHolder.setMouseTransparent(false) must restore picking");
        require(z(major) > z(minor), "major and minor grids need separate depth layers");
        require(Math.abs(z(minor) - 0.04) < EPS && Math.abs(z(major) - 0.08) < EPS,
                "grid depths must be 0.04 and 0.08 mm");

        require(boundsContain(major, -90, 90) && boundsContain(minor, -90, 90),
                "grid meshes must cover the physical +/-90 mm plane");
        require(((PhongMaterial) major.getMaterial()).getSelfIlluminationMap() != null,
                "major grid must use flat self-illumination");
        require(((PhongMaterial) minor.getMaterial()).getSelfIlluminationMap() != null,
                "minor grid must use flat self-illumination");
        require(outline.getChildren().get(0) instanceof MeshView,
                "outline must be a mesh");
        require(Math.abs(grid.getBackgroundOpacity() - 1.0) < EPS
                        && Math.abs(background.getOpacity() - 1.0) < EPS
                        && Math.abs(((PhongMaterial) grid.intersectionNode.getMaterial())
                                .getDiffuseColor().getOpacity() - 1.0) < EPS,
                "generic GridHolder backgrounds must remain opaque by default");
        grid.setBackgroundOpacity(0.20);
        require(Math.abs(grid.getBackgroundOpacity() - 0.20) < EPS
                        && Math.abs(background.getOpacity() - 1.0) < EPS,
                "background opacity must use one material alpha layer");
        PhongMaterial backgroundMaterial = (PhongMaterial) mesh(background.getChildren().get(0)).getMaterial();
        require(Math.abs(backgroundMaterial.getDiffuseColor().getOpacity() - 0.20) < EPS
                        && Math.abs(grid.intersectionNode.getOpacity() - 1.0) < EPS
                        && backgroundMaterial.getSelfIlluminationMap() != null,
                "background material must retain self-illumination with diffuse alpha");
        require(!grid.intersectionNode.isMouseTransparent(),
                "background opacity must not disable workplane picking");
        checkEmission(grid, 0.20);
        grid.setBackgroundOpacity(0.20);
        checkEmission(grid, 0.20); // Repeated changes must not darken the previous map.

        double[] baseline = coordinates(major, minor);
        grid.mmOffset.setTx(37);
        grid.mmOffset.setTy(-19);
        for (double snap : new double[] {0.1, 7, 10}) {
            grid.setSnap(snap);
            same(baseline, coordinates(major, minor));
        }
        BowlerStudio3dEngine.makeGrid(grid);
        Group remade = group(grid, "bigGridView");
        same(baseline, coordinates(mesh(remade.getChildren().get(0)), mesh(remade.getChildren().get(1))));
        Group remadeBackground = group(grid, "backgroundView");
        require(Math.abs(grid.getBackgroundOpacity() - 0.20) < EPS
                        && Math.abs(remadeBackground.getOpacity() - 1.0) < EPS
                        && Math.abs(grid.intersectionNode.getOpacity() - 1.0) < EPS
                        && Math.abs(((PhongMaterial) mesh(remadeBackground.getChildren().get(0)).getMaterial())
                                .getDiffuseColor().getOpacity() - 0.20) < EPS,
                "background material alpha must persist through makeGrid rebuild");
        require(!grid.intersectionNode.isMouseTransparent(),
                "makeGrid rebuild must keep the new intersection node pickable");
        checkEmission(grid, 0.20);
        grid.setBackgroundOpacity(1.0);
        checkEmission(grid, 1.0);
        checkSurfacePicking();
        System.out.println("PASS: 180 mm workplane, physical grid extent, depth layers, flat materials and picking");
    }

    private static Group group(BowlerStudio3dEngine.GridHolder grid, String name) throws Exception {
        var field = BowlerStudio3dEngine.GridHolder.class.getDeclaredField(name);
        field.setAccessible(true);
        return (Group) field.get(grid);
    }

    private static void checkEmission(BowlerStudio3dEngine.GridHolder grid, double opacity) {
        PhongMaterial material = (PhongMaterial) grid.intersectionNode.getMaterial();
        Color ink = material.getSelfIlluminationMap().getPixelReader().getColor(0, 0);
        Color base = BowlerStudio3dEngine.getLightGrid();
        double tolerance = 1.0 / 255;
        require(Math.abs(ink.getRed() - base.getRed() * opacity) < tolerance
                        && Math.abs(ink.getGreen() - base.getGreen() * opacity) < tolerance
                        && Math.abs(ink.getBlue() - base.getBlue() * opacity) < tolerance,
                "self-illumination must be premultiplied by opacity to avoid hiding the model");
    }

    private static void checkSurfacePicking() throws Exception {
        var pickNormal = com.commonwealthrobotics.WorkplaneManager.class.getDeclaredMethod(
                "getFaceNormalAngles", TriangleMesh.class, int.class);
        pickNormal.setAccessible(true);
        var cube = new eu.mihosoft.vrl.v3d.Cube(20, 20, 20).toCSG();
        MeshView view = cube.getMesh();
        TriangleMesh original = (TriangleMesh) view.getMesh();
        com.commonwealthrobotics.kloster.WorkshopSurface.prepare(view);
        TriangleMesh shaded = (TriangleMesh) view.getMesh();
        int count = original.getFaces().size() / original.getFaceElementSize();
        for (int face = 0; face < count; face++) {
            var before = (eu.mihosoft.vrl.v3d.Polygon) pickNormal.invoke(null, original, face);
            var after = (eu.mihosoft.vrl.v3d.Polygon) pickNormal.invoke(null, shaded, face);
            require(before.getPlane().getNormal().minus(after.getPlane().getNormal()).magnitude() < EPS,
                    "shading changed workplane placement orientation for face " + face);
        }
    }

    private static MeshView mesh(Node node) {
        require(node instanceof MeshView, "grid child must be a MeshView");
        return (MeshView) node;
    }

    private static double z(MeshView mesh) {
        double value = mesh.getTranslateZ();
        for (Transform transform : mesh.getTransforms()) value += transform.getTz();
        return value;
    }

    private static boolean boundsContain(MeshView mesh, double min, double max) {
        var b = mesh.getBoundsInLocal();
        return b.getMinX() <= min + 0.01 && b.getMaxX() >= max - 0.01
                && b.getMinY() <= min + 0.01 && b.getMaxY() >= max - 0.01;
    }

    private static double[] coordinates(MeshView... views) {
        var values = new java.util.ArrayList<Double>();
        for (MeshView view : views) {
            var points = ((TriangleMesh) view.getMesh()).getPoints();
            for (int i = 0; i < points.size(); i += 3) {
                var point = view.localToParent(points.get(i), points.get(i + 1), points.get(i + 2));
                values.add(point.getX()); values.add(point.getY()); values.add(point.getZ());
            }
        }
        return values.stream().mapToDouble(Double::doubleValue).toArray();
    }

    private static void same(double[] expected, double[] actual) {
        require(expected.length == actual.length, "grid topology changed after snap");
        for (int i = 0; i < expected.length; i++) require(Math.abs(expected[i] - actual[i]) < EPS,
                "snap and mmOffset changed a physical grid vertex at " + i);
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
}
