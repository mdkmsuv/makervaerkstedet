package com.commonwealthrobotics.kloster;

import java.io.IOException;
import java.io.Reader;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.Properties;

/** A bookmark for the guide, separate from the model and its undo history. */
final class LearningProgress {
    record State(int step, boolean completed) { }

    static State read(Path project, String id, int count) {
        if (project == null) return new State(0, false);
        try (Reader input = Files.newBufferedReader(path(project), StandardCharsets.UTF_8)) {
            Properties properties = new Properties(); properties.load(input);
            if (!id.equals(properties.getProperty("course")) || !"2".equals(properties.getProperty("version")))
                return new State(0, false);
            int step = Integer.parseInt(properties.getProperty("step", "0"));
            if (step < 0 || step >= count) return new State(0, false);
            return new State(step, Boolean.parseBoolean(properties.getProperty("completed")));
        } catch (IOException | IllegalArgumentException invalid) { return new State(0, false); }
    }

    static void write(Path project, String id, State state) throws IOException {
        if (project == null) return;
        Path target = path(project);
        Path temporary = Files.createTempFile(target.toAbsolutePath().getParent(), "guide-", ".tmp");
        try {
            Properties properties = new Properties();
            properties.setProperty("version", "2"); properties.setProperty("course", id);
            properties.setProperty("step", Integer.toString(state.step()));
            properties.setProperty("completed", Boolean.toString(state.completed()));
            try (Writer output = Files.newBufferedWriter(temporary, StandardCharsets.UTF_8)) {
                properties.store(output, "CaDoodle guide bookmark");
            }
            try { Files.move(temporary, target, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE); }
            catch (AtomicMoveNotSupportedException unsupported) {
                Files.move(temporary, target, StandardCopyOption.REPLACE_EXISTING);
            }
        } finally { Files.deleteIfExists(temporary); }
    }

    private static Path path(Path project) {
        return project.resolveSibling(project.getFileName() + ".learning-progress.properties");
    }
}
