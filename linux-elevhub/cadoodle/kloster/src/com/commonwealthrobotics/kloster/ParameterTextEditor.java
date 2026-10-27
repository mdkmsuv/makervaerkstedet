package com.commonwealthrobotics.kloster;

import java.util.Objects;
import java.util.function.Consumer;
import javafx.scene.control.TextField;
import javafx.scene.input.KeyCode;
import javafx.scene.input.KeyEvent;

/** Native free-text parameters keep their accepted text while geometry runs. */
public final class ParameterTextEditor {
    private ParameterTextEditor() { }

    public static TextField create(String initial, Consumer<String> apply) {
        TextField field = new TextField(initial == null ? "" : initial);
        String[] accepted = {field.getText()};
        boolean[] committing = {false};
        Runnable commit = () -> {
            if (field.isDisabled() || committing[0]) return;
            String value = field.getText();
            if (Objects.equals(value, accepted[0])) return;
            committing[0] = true;
            try { apply.accept(value); accepted[0] = value; }
            finally { committing[0] = false; }
        };
        field.setOnAction(event -> commit.run());
        field.focusedProperty().addListener((observable, before, after) -> {
            if (!after) commit.run();
        });
        field.addEventFilter(KeyEvent.KEY_PRESSED, event -> {
            if (event.getCode() == KeyCode.ESCAPE) {
                field.setText(accepted[0]); event.consume();
            } else if (event.getCode() == KeyCode.ENTER) {
                commit.run(); event.consume();
            } else if (event.getCode() == KeyCode.TAB) {
                commit.run();
            }
        });
        return field;
    }
}
