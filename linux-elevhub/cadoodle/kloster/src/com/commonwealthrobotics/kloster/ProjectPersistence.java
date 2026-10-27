package com.commonwealthrobotics.kloster;

import com.google.gson.JsonParser;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.CaDoodleFile;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;

/** Check the editable project itself, including edits that do not request autosave. */
public final class ProjectPersistence {
    private ProjectPersistence() { }

    public static boolean matchesDisk(CaDoodleFile file) {
        if (file == null || file.getSelf() == null) return false;
        try {
            String stored = Files.readString(file.getSelf().toPath(), StandardCharsets.UTF_8);
            return JsonParser.parseString(file.toJson()).equals(JsonParser.parseString(stored));
        } catch (Exception failure) {
            // A missing, incomplete or unreadable file is never evidence of a saved project.
            return false;
        }
    }
}
