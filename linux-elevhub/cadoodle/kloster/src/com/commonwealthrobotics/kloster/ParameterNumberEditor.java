package com.commonwealthrobotics.kloster;

import java.util.List;
import java.util.Locale;
import java.util.function.DoubleConsumer;
import java.util.function.DoubleSupplier;
import javafx.scene.control.ComboBox;
import javafx.scene.control.Control;
import javafx.scene.control.TextField;
import javafx.scene.input.KeyCode;
import javafx.scene.input.KeyEvent;

/** Decimal editing shared by native parametric tools. All callbacks run on FX. */
public final class ParameterNumberEditor {
    private ParameterNumberEditor() { }

    public static Control create(List<String> choices, double maximum,
            DoubleSupplier current, DoubleConsumer apply) {
        if (choices.size() < 2) {
            TextField field = new TextField(format(current.getAsDouble()));
            Editor editor = new Editor(field, -maximum, maximum, current, apply);
            field.setOnAction(event -> editor.commit());
            return field;
        }
        ComboBox<String> box = new ComboBox<>();
        double minimum = Double.POSITIVE_INFINITY, maximumChoice = Double.NEGATIVE_INFINITY;
        for (String choice : choices) {
            double value = parse(choice);
            minimum = Math.min(minimum, value);
            maximumChoice = Math.max(maximumChoice, value);
            box.getItems().add(format(value));
        }
        box.setEditable(true);
        String initial = format(current.getAsDouble());
        box.setValue(initial);
        box.getEditor().setText(initial);
        Editor editor = new Editor(box.getEditor(), minimum, maximumChoice, current, apply);
        editor.synchronizeValue = box::setValue;
        // Native ComboBox list changes and editor commits arrive by different
        // paths. Read the list value only for an actual list/value change.
        box.valueProperty().addListener((observable, previous, value) -> {
            if (value != null && !editor.writing) {
                editor.write(value);
                editor.commit();
            }
        });
        box.getEditor().setOnAction(event -> { editor.commit(); event.consume(); });
        box.setOnAction(event -> editor.commit());
        return box;
    }

    private static String format(double value) {
        // Preserve precision when merely tabbing through an existing value.
        return Double.toString(value).replace('.',
            java.text.DecimalFormatSymbols.getInstance(Locale.getDefault()).getDecimalSeparator());
    }

    private static double parse(String text) {
        double value = Double.parseDouble(text.strip().replace(',', '.'));
        if (!Double.isFinite(value)) throw new NumberFormatException("Finite number required");
        return value;
    }

    private static final class Editor {
        final TextField field;
        final double minimum, maximum;
        final DoubleSupplier current;
        final DoubleConsumer apply;
        double accepted;
        java.util.function.Consumer<String> synchronizeValue = ignored -> { };
        boolean writing, committing;

        Editor(TextField field, double minimum, double maximum,
                DoubleSupplier current, DoubleConsumer apply) {
            this.field = field; this.minimum = minimum; this.maximum = maximum;
            this.current = current; this.apply = apply;
            this.accepted = current.getAsDouble();
            field.focusedProperty().addListener((o, before, after) -> { if (!after) commit(); });
            field.addEventFilter(KeyEvent.KEY_PRESSED, event -> {
                if (event.getCode() == KeyCode.ESCAPE) {
                    write(format(accepted)); event.consume();
                } else if (event.getCode() == KeyCode.TAB) {
                    commit(); // Also works before a scene has transferred focus.
                } else if (event.getCode() == KeyCode.ENTER) {
                    commit(); event.consume();
                }
            });
        }

        void write(String text) {
            writing = true;
            try {
                field.setText(text);
                // Keep the ComboBox value in step with manually entered text.
                // Otherwise choosing its former selection emits no value change.
                synchronizeValue.accept(text);
            } finally { writing = false; }
        }

        void commit() {
            if (writing || committing || field.isDisabled()) return;
            committing = true;
            try {
                double value = Math.max(minimum, Math.min(maximum, parse(field.getText())));
                if (Double.compare(value, accepted) != 0) {
                    apply.accept(value);
                    accepted = value;
                }
                // The model may regenerate on a worker. Keep the accepted
                // value visible and avoid resubmitting it on focus loss.
                write(format(accepted));
            } catch (NumberFormatException invalid) {
                write(format(accepted));
            } finally { committing = false; }
        }
    }
}
