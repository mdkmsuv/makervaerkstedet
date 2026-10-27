package com.commonwealthrobotics.kloster;

import com.neuronrobotics.bowlerstudio.creature.NoImageException;
import com.neuronrobotics.bowlerstudio.creature.ThumbnailImage;
import eu.mihosoft.vrl.v3d.CSG;
import eu.mihosoft.vrl.v3d.parametrics.CSGDatabaseInstance;
import javafx.scene.image.WritableImage;

import java.io.File;
import java.io.IOException;
import java.util.List;

/**
 * Per-request image provider for CaDoodle file thumbnails.
 *
 * ThumbnailImage owns a JavaFX Group and mutable view/CSG lists.  It therefore
 * must never be shared by concurrent CaDoodleFile image requests.
 */
public final class WorkshopThumbnails {
    private WorkshopThumbnails() {
    }

    public static WritableImage get(CSGDatabaseInstance instance,
                                    List<CSG> incomingToDisplay,
                                    File destination)
            throws NoImageException, IOException {
        return new ThumbnailImage().get(instance, incomingToDisplay, destination);
    }
}
