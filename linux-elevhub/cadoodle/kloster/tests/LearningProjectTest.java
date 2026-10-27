import com.commonwealthrobotics.kloster.DimensionResize;
import com.commonwealthrobotics.kloster.LearningProjectService;
import com.commonwealthrobotics.kloster.LearningModels;
import com.commonwealthrobotics.kloster.PrintModel;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.CaDoodleFile;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.MoveCenter;
import com.neuronrobotics.sdk.addons.kinematics.math.TransformNR;
import eu.mihosoft.vrl.v3d.CSG;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.Comparator;
import java.util.List;

/** Headless save/reopen/native edit/export check for learning starter projects. */
public final class LearningProjectTest {
    public static void main(String[] args) throws Exception {
        Path root = Files.createTempDirectory("learning-project-test-");
        Path doodle = root.resolve("NameTag.doodle");
        CaDoodleFile created = LearningProjectService.create(doodle.toFile(), "name-tag", "Ada");
        require("name-tag".equals(LearningProjectService.starterId(created)), "metadata id missing");
        require(LearningProjectService.hasStepGuide(created), "new starter missing guide marker");
        CaDoodleFile reopened = CaDoodleFile.fromFile(doodle.toFile());
        Thread regenerated = reopened.regenerateAll();
        if (regenerated != null) regenerated.join();
        require(reopened.getCurrentState().stream().noneMatch(CSG::isGroupResult), "starter unexpectedly grouped parts");
        Path stl = root.resolve("name-tag.stl");
        PrintModel.write(stl, reopened.getCurrentState());
        require(Files.size(stl) > 100, "STL export is empty");

        CSG part = reopened.getCurrentState().stream().filter(c -> !c.isInGroup() && !c.isHole()).findFirst().orElseThrow();
        double x = part.getCenterX();
        Thread moved = reopened.addOperation(new MoveCenter().setNames(List.of(part.getName()), reopened)
                .setLocation(new TransformNR(3, 0, 0)));
        if (moved != null) moved.join();
        CSG movedPart = CaDoodleFile.getByName(reopened.getCurrentState(), part.getName());
        require(Math.abs(movedPart.getCenterX() - x - 3) < .01, "native move changed wrong amount");
        double beforeHeight = movedPart.getTotalZ();
        var resize = DimensionResize.create(List.of(movedPart), new TransformNR(), 2, beforeHeight + 1, .5, .5, false);
        require(resize != null, "resize was skipped");
        Thread resized = reopened.addOperation(resize);
        if (resized != null) resized.join();
        require(Math.abs(CaDoodleFile.getByName(reopened.getCurrentState(), part.getName()).getTotalZ() - beforeHeight - 1) < .01,
                "resize did not set requested height");
        require(reopened.isBackAvailable(), "native edit timeline cannot undo");
        reopened.back();
        require(Math.abs(CaDoodleFile.getByName(reopened.getCurrentState(), part.getName()).getTotalZ() - beforeHeight) < .01,
                "undo did not restore previous dimensions");
        require(reopened.isForwardAvailable(), "native edit timeline cannot redo");
        reopened.forward();
        require(Math.abs(CaDoodleFile.getByName(reopened.getCurrentState(), part.getName()).getTotalZ() - beforeHeight - 1) < .01,
                "redo did not restore edited dimensions");
        // Save edited history independently; keep the initial file for the relocation check below.
        Path edited = root.resolve("Edited.doodle");
        reopened.setSelf(edited.toFile()).save(true);
        CaDoodleFile editedReopened = CaDoodleFile.fromFile(edited.toFile());
        Thread edits = editedReopened.regenerateAll(); if (edits != null) edits.join();
        CSG editedPart = CaDoodleFile.getByName(editedReopened.getCurrentState(), part.getName());
        require(Math.abs(editedPart.getTotalZ() - beforeHeight - 1) < .01 && Math.abs(editedPart.getCenterX() - x - 3) < .01,
                "save/reopen lost pupil edits");
        editedReopened.close();
        reopened.close();
        portabilityCheck(root, "name-tag", "Ada");
        for (String id : List.of("robot", "game-piece", "pencil-holder", "desk-sign", "open-box")) {
            Path other = root.resolve(id + ".doodle");
            CaDoodleFile starter = LearningProjectService.create(other.toFile(), id, "Test");
            require(id.equals(LearningProjectService.starterId(starter)), id + " metadata id missing");
            CaDoodleFile restored = CaDoodleFile.fromFile(other.toFile());
            Thread all = restored.regenerateAll();
            if (all != null) all.join();
            require(!restored.getCurrentState().isEmpty(), id + " reopened empty");
            compareParts(restored, id, "Test");
            Path exported = root.resolve(id + "-export.stl");
            PrintModel.write(exported, restored.getCurrentState());
            require(Files.size(exported) > 100, id + " STL export is empty");
            require(restored.getCurrentState().stream().noneMatch(CSG::isGroupResult), id + " starter unexpectedly grouped parts");
            require(LearningProjectService.hasStepGuide(restored), id + " guide marker missing");
            checkLooseLayout(id);
            restored.close(); starter.close();
        }
        System.out.println("PASS: learning project save/reopen/regenerate/group/edit/undo/redo/STL");
    }

    private static void portabilityCheck(Path root, String id, String label) throws Exception {
        Path source = root.resolve("NameTag.doodle");
        String json = Files.readString(source);
        require(!json.contains(root.toString()),
                "serialized starter contains staging or absolute source path");
        Path copied = root.resolve("copied");
        Files.createDirectories(copied);
        Files.copy(source, copied.resolve(source.getFileName()), StandardCopyOption.REPLACE_EXISTING);
        Files.copy(root.resolve("NameTag.properties"), copied.resolve("NameTag.properties"), StandardCopyOption.REPLACE_EXISTING);
        try (var files = Files.list(root)) {
            for (Path file : files.filter(p -> p.getFileName().toString().endsWith(".stl")).toList())
                Files.copy(file, copied.resolve(file.getFileName()), StandardCopyOption.REPLACE_EXISTING);
        }
        com.neuronrobotics.bowlerstudio.util.FileChangeWatcher.clearAll();
        Files.delete(source);
        Files.delete(root.resolve("NameTag.properties"));
        try (var files = Files.list(root)) {
            for (Path file : files.filter(p -> p.getFileName().toString().endsWith(".stl")).toList())
                Files.delete(file);
        }
        CaDoodleFile moved = CaDoodleFile.fromFile(copied.resolve("NameTag.doodle").toFile());
        Thread regenerated = moved.regenerateAll();
        if (regenerated != null) regenerated.join();
        compareParts(moved, id, label);
        moved.close();
    }

    private static void compareParts(CaDoodleFile moved, String id, String label) throws Exception {
        List<CSG> expected = LearningProjectService.starterParts(id, label);
        require(moved.getCurrentState().size() >= expected.size(), "project lost frozen parts");
        for (CSG wanted : expected) {
            CSG got = moved.getCurrentState().stream()
                    .filter(c -> wanted.getName().equals(c.getUserDefinedName())).findFirst().orElseThrow();
            require(Math.abs(got.getTotalX() - wanted.getTotalX()) < .01
                    && Math.abs(got.getTotalY() - wanted.getTotalY()) < .01
                    && Math.abs(got.getTotalZ() - wanted.getTotalZ()) < .01
                    && Math.abs(got.getMinX() - wanted.getMinX()) < .01
                    && Math.abs(got.getMinY() - wanted.getMinY()) < .01
                    && Math.abs(got.getMinZ() - wanted.getMinZ()) < .01
                    && got.isHole() == wanted.isHole()
                    && got.getColor().equals(wanted.getColor()),
                    "copied project geometry changed for " + wanted.getName());
        }
    }

    private static void checkLooseLayout(String id) throws Exception {
        List<CSG> parts = LearningProjectService.starterParts(id, "Test");
        for (int i = 0; i < parts.size(); i++) {
            var current = parts.get(i).getBounds();
            require(current.getMinZ() >= -0.001 && current.getMaxX() <= 175.001
                    && current.getMaxY() <= 175.001 && current.getMaxZ() <= 175.001,
                    id + " part outside 175 mm starter bounds");
            for (int j = i + 1; j < parts.size(); j++) {
                var a = parts.get(i).getBounds();
                var b = parts.get(j).getBounds();
                boolean overlap = a.getMaxX() > b.getMinX() + .001 && b.getMaxX() > a.getMinX() + .001
                        && a.getMaxY() > b.getMinY() + .001 && b.getMaxY() > a.getMinY() + .001;
                require(!overlap, id + " loose parts overlap in XY");
            }
        }
    }

    private static void deleteTree(Path root) throws Exception {
        if (Files.isDirectory(root)) {
            try (var paths = Files.walk(root)) {
                paths.sorted((a, b) -> b.getNameCount() - a.getNameCount()).forEach(p -> {
                    try { Files.deleteIfExists(p); } catch (Exception ignored) { }
                });
            }
        } else try { Files.deleteIfExists(root); } catch (Exception ignored) { }
    }

    private static void require(boolean ok, String message) throws Exception {
        if (!ok) throw new Exception(message);
    }
}
