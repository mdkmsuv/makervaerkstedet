package com.commonwealthrobotics.kloster;

import com.neuronrobotics.bowlerstudio.scripting.cadoodle.AddFromFile;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.CaDoodleFile;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.SetUserDefinedName;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.ToSolid;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.ToHole;
import eu.mihosoft.vrl.v3d.CSG;
import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/** Builds an offline, native CaDoodle starter without changing the active project. */
public final class LearningProjectService {
    private LearningProjectService() { }

    /** Raw editable parts, with the loose-part starters laid out for independent editing. */
    public static List<CSG> starterParts(String id, String label) {
        List<CSG> parts = LearningModels.create(id, label);
        List<CSG> laidOut = new ArrayList<>();
        for (int i = 0; i < parts.size(); i++) {
            CSG part = parts.get(i);
            var bounds = part.getBounds();
            double targetX = (i % 2) * 85.0 + 5.0;
            double targetY = (i / 2) * 55.0 + 5.0;
            laidOut.add(part.movex(targetX - bounds.getMinX()).movey(targetY - bounds.getMinY())
                    .movez(-bounds.getMinZ()));
        }
        return List.copyOf(laidOut);
    }

    public static CaDoodleFile create(File destinationDoodle, String id, String label) throws Exception {
        if (destinationDoodle == null || destinationDoodle.getName().isBlank())
            throw new IllegalArgumentException("Projektfil mangler");
        LearningCatalog.Entry entry = LearningCatalog.find(id);
        Path destination = destinationDoodle.toPath().toAbsolutePath();
        Files.createDirectories(destination.getParent());
        if (Files.exists(destination)) throw new IOException("Projektfilen findes allerede: " + destination);
        String projectName = !entry.hasText() || label == null || label.isBlank() ? entry.title() : entry.title() + " - " + label.strip();
        CaDoodleFile project = new CaDoodleFile().setSelf(destination.toFile()).setProjectName(projectName);
        project.initialize();
        if (!project.isInitialized()) throw new IOException("Projektet kunne ikke initialiseres");

        Path staging = Files.createTempDirectory("cadoodle-learning-source-");
        try {
            List<CSG> expected = starterParts(id, label);
            List<String> names = new ArrayList<>();
            for (int i = 0; i < expected.size(); i++) {
                // Freeze each independent solid into the project. Regeneration must not
                // depend on later template code, installed fonts, or a source repository.
                Path source = staging.resolve("part-" + i + ".stl");
                Files.writeString(source, expected.get(i).toStlString(), StandardCharsets.UTF_8);
                AddFromFile operation = new AddFromFile().set(source.toFile(), project).setPreventBoM(true);
                Thread thread = project.addOperation(operation);
                join(thread);
                List<String> added = operation.getNamesAddedInThisOperation();
                if (added == null || added.size() != 1)
                    throw new IOException("Starterdelen blev ikke oprettet: " + id + " / " + i);
                names.add(added.get(0));
                join(project.addOperation(new ToSolid().setNames(added).setColor(expected.get(i).getColor())));
                if (expected.get(i).isHole()) join(project.addOperation(new ToHole().setNames(added)));
                SetUserDefinedName rename = new SetUserDefinedName().setTarget(added.get(0))
                        .setNewUserDefinedName(expected.get(i).getName());
                Thread renamed = project.addOperation(rename);
                join(renamed);
            }
            requireExpectedNames(project.getCurrentState(), expected);
            project.save(true);
            Files.writeString(metadata(destination), "id=" + id + System.lineSeparator()
                    + "guide=2" + System.lineSeparator(), StandardCharsets.UTF_8);
            return project;
        } finally {
            deleteTree(staging);
        }
    }

    public static String starterId(CaDoodleFile project) throws IOException {
        if (project == null || project.getSelf() == null) return null;
        Path metadata = metadata(project.getSelf().toPath());
        if (!Files.isRegularFile(metadata)) return null;
        for (String line : Files.readAllLines(metadata, StandardCharsets.UTF_8)) {
            if (line.startsWith("id=")) {
                String id = line.substring(3).trim();
                try { LearningCatalog.find(id); return id; }
                catch (IllegalArgumentException ignored) { return null; }
            }
        }
        return null;
    }

    /** True only for newly created starters carrying the loose-parts step guide marker. */
    public static boolean hasStepGuide(CaDoodleFile project) throws IOException {
        if (project == null || project.getSelf() == null) return false;
        Path metadata = metadata(project.getSelf().toPath());
        if (!Files.isRegularFile(metadata)) return false;
        return Files.readAllLines(metadata, StandardCharsets.UTF_8).stream()
                .anyMatch(line -> line.trim().equals("guide=2"));
    }

    private static void requireExpectedNames(List<CSG> actual, List<CSG> expected) throws IOException {
        for (CSG wanted : expected) {
            CSG got = actual.stream().filter(c -> wanted.getName().equals(c.getUserDefinedName())
                    || wanted.getName().equals(c.getName())).findFirst().orElse(null);
            if (got == null) throw new IOException("Starterdelen mangler efter import: " + wanted.getName());
            if (Math.abs(got.getTotalX() - wanted.getTotalX()) > 0.01
                    || Math.abs(got.getTotalY() - wanted.getTotalY()) > 0.01
                    || Math.abs(got.getTotalZ() - wanted.getTotalZ()) > 0.01
                    || Math.abs(got.getMinX() - wanted.getMinX()) > 0.01
                    || Math.abs(got.getMinY() - wanted.getMinY()) > 0.01
                    || Math.abs(got.getMinZ() - wanted.getMinZ()) > 0.01
                    || !got.getColor().equals(wanted.getColor())
                    || got.isHole() != wanted.isHole())
                throw new IOException("Starterdelen har forkert geometri: " + wanted.getName()
                        + " expected=" + wanted.getTotalX() + "x" + wanted.getTotalY() + "x" + wanted.getTotalZ()
                        + " actual=" + got.getTotalX() + "x" + got.getTotalY() + "x" + got.getTotalZ());
        }
    }

    private static void join(Thread thread) throws IOException {
        if (thread == null) return;
        try {
            thread.join(30000);
        } catch (InterruptedException ex) {
            Thread.currentThread().interrupt();
            throw new IOException("Starteroperationen blev afbrudt", ex);
        }
        if (thread.isAlive()) throw new IOException("Starteroperationen tog for lang tid");
    }

    private static Path metadata(Path doodle) {
        return doodle.resolveSibling(doodle.getFileName().toString().replaceFirst("\\.doodle$", ".properties"));
    }

    private static void deleteTree(Path root) {
        try (var paths = Files.walk(root)) {
            paths.sorted((a, b) -> b.getNameCount() - a.getNameCount()).forEach(path -> {
                try { Files.deleteIfExists(path); } catch (IOException ignored) { }
            });
        } catch (IOException ignored) { }
    }
}
