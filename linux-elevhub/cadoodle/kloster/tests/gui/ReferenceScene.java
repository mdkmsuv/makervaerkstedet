package com.commonwealthrobotics.kloster;

import com.neuronrobotics.bowlerstudio.scripting.cadoodle.AddFromScript;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.CaDoodleFile;
import eu.mihosoft.vrl.v3d.CSG;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import javafx.scene.paint.Color;

/** Creates and reopens a portable CaDoodle reference project for GUI captures. */
public final class ReferenceScene {
    private static final String REPOSITORY = "reference-repo";
    private static final String SCRIPT = "reference.groovy";
    private static final String SCRIPT_TEXT = """
            import eu.mihosoft.vrl.v3d.*
            import javafx.scene.paint.Color

            def plate = new Cube(70,22,3).toCSG().toZMin().movey(30)
            def plateHole = new Cylinder(6,5).toCSG().toZMin().movey(30)
            plate = plate.difference(plateHole).setColor(Color.RED)

            def sphere = new Sphere(10,32,16).toCSG()
                    .movex(-22).movey(-22).movez(10)
                    .setColor(Color.color(0.10,0.72,0.78,1.0))

            def cube = new Cube(22,22,22).toCSG().toZMin()
                    .movex(22).movey(-22)
                    .setColor(Color.RED)

            // A separate hole tool stays visible in the editor and is omitted from STL.
            def holeTool = new Cylinder(10,24).toCSG().toZMin()
                    .movex(22).movey(-22).movez(-1)
                    .setColor(Color.color(0.50,0.54,0.58,0.58))
                    .setIsHole(true)

            return [plate, sphere, cube, holeTool]
            """;

    private ReferenceScene() {}

    public static void main(String[] args) throws Exception {
        if (args.length != 1) throw new IllegalArgumentException("Usage: ReferenceScene <reference-project directory>");
        Path root = Path.of(args[0]).toAbsolutePath();
        Path home = root.resolve("fixture-home");
        Path repository = root.resolve(REPOSITORY);
        Path projectFile = root.resolve("reference-project.doodle");
        Files.createDirectories(repository);
        Files.createDirectories(home);
        Files.writeString(repository.resolve(SCRIPT), SCRIPT_TEXT, StandardCharsets.UTF_8);
        Files.deleteIfExists(projectFile);
        System.setProperty("user.home", home.toString());

        CaDoodleFile project = new CaDoodleFile().setSelf(projectFile.toFile()).setProjectName("Tinkercad reference scene");
        project.initialize();
        AddFromScript operation = new AddFromScript().set(REPOSITORY, SCRIPT).setPreventBoM(true);
        Thread added = project.addOperation(operation);
        if (added != null) added.join();
        require(project.getCurrentState().size() == 4, "script did not create four shapes");
        project.save(true);

        String json = Files.readString(projectFile, StandardCharsets.UTF_8);
        require(json.contains(REPOSITORY) && !json.contains(home.toString()), "fixture contains a personal home path");

        CaDoodleFile reopened = CaDoodleFile.fromFile(projectFile.toFile());
        Thread regenerated = reopened.regenerateAll();
        if (regenerated != null) regenerated.join();
        List<CSG> state = reopened.getCurrentState();
        require(state.size() == 4, "reopened fixture did not regenerate four shapes");
        require(state.stream().anyMatch(CSG::isHole), "reopened fixture lost the hole tool");
        require(state.stream().anyMatch(shape -> close(shape.getTotalX(), 70) && close(shape.getTotalY(), 22)
                && close(shape.getTotalZ(), 3)), "plate dimensions changed");
        require(state.stream().anyMatch(shape -> close(shape.getTotalX(), 20) && close(shape.getTotalY(), 20)
                && close(shape.getTotalZ(), 20)), "sphere dimensions changed");
        require(state.stream().anyMatch(shape -> !shape.isHole() && close(shape.getTotalX(), 22)
                && close(shape.getTotalY(), 22) && close(shape.getTotalZ(), 22)), "cube dimensions changed");

        // CaDoodle intentionally disables back/forward when only one operation exists;
        // keep this fixture atomic so the four reference shapes load together.
        require(reopened.getOperations().size() == 1 && !reopened.isBackAvailable(),
                "reference fixture should contain one atomic operation");

        Path stl = root.resolve("reference-visible-solids.stl");
        PrintModel.write(stl, state);
        require(Files.size(stl) > 100, "reference solids did not produce STL output");
        System.out.println("ReferenceScene PASS: " + projectFile);
    }

    private static boolean close(double actual, double expected) {
        return Math.abs(actual - expected) < 0.01;
    }

    private static void require(boolean condition, String message) throws IOException {
        if (!condition) throw new IOException(message);
    }
}
