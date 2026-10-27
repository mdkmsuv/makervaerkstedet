package com.commonwealthrobotics.kloster.tests.gui;

import com.commonwealthrobotics.RulerManager;
import com.commonwealthrobotics.numbers.TextFieldDimension;
import com.commonwealthrobotics.numbers.ThreedNumber;
import eu.mihosoft.vrl.v3d.Vector3d;
import javafx.application.Platform;
import javafx.scene.control.TextField;
import javafx.scene.input.KeyCode;
import javafx.scene.input.KeyEvent;
import javafx.scene.transform.Affine;

import java.util.Locale;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/** Headless JavaFX probe for ThreedNumber commit and cancellation semantics. */
public final class DimensionInputProbe {
    private DimensionInputProbe() { }

    public static void main(String[] args) throws Exception {
        CountDownLatch finished = new CountDownLatch(1);
        AtomicReference<Throwable> failure = new AtomicReference<>();
        Platform.startup(() -> {
            try {
                // Both separators are valid in both locales. The formatter is
                // deliberately locale independent; validation canonicalizes
                // the value before parsing.
                probeLocale(Locale.forLanguageTag("da-DK"), "12,500");
                probeLocale(Locale.forLanguageTag("da-DK"), "12.500");
                probeLocale(Locale.US, "12,500");
                probeLocale(Locale.US, "12.500");
            } catch (Throwable ex) {
                failure.set(ex);
            } finally {
                finished.countDown();
            }
        });
        if (!finished.await(45, TimeUnit.SECONDS)) throw new AssertionError("Input probe timed out");
        Throwable ex = failure.get();
        try {
            if (ex != null) throw new AssertionError("DimensionInputProbe failed", ex);
            probePendingRefresh();
        } finally { Platform.exit(); }
        System.out.println("DimensionInputProbe: passed");
    }

    private static void probePendingRefresh() throws Exception {
        ThreedNumber[] number = new ThreedNumber[1];
        TextField[] field = new TextField[1];
        runFx(() -> {
            number[0] = new ThreedNumber(new Affine(), new Affine(), () -> {},
                TextFieldDimension.None, new RulerManager(null), 4, new Vector3d(1, 0, 0));
            field[0] = (TextField) number[0].getTextField().getFirst();
            number[0].show();
            Thread refresh = new Thread(() -> number[0].setValue(99));
            refresh.start();
            try { refresh.join(2000); }
            catch (InterruptedException error) { throw new AssertionError(error); }
            require(!refresh.isAlive(), "background field update blocked on FX");
            field[0].setText("17.5");
        });
        runFx(() -> {
            require(field[0].getText().equals("17.5"), "pending refresh overwrote new edit");
            fire(field[0], KeyCode.ENTER);
            require(number[0].getMostRecentValue() == 17.5, "pending refresh lost committed value");
        });
    }

    private static void runFx(Runnable action) throws Exception {
        var task = new java.util.concurrent.FutureTask<Void>(action, null);
        Platform.runLater(task);
        task.get(5, TimeUnit.SECONDS);
    }

    private static void probeLocale(Locale locale, String decimalText) {
        Locale previous = Locale.getDefault();
        Locale.setDefault(locale);
        boolean comma = decimalText.contains(",");
        try {
            ProbeState state = new ProbeState();
            AtomicReference<ThreedNumber> numberRef = new AtomicReference<>();
            ThreedNumber number = new ThreedNumber(new Affine(), new Affine(),
                    () -> state.commit(numberValue(state, numberRef.get())), TextFieldDimension.None,
                    new RulerManager(null), 4, new Vector3d(1, 0, 0));
            numberRef.set(number);
            TextField field = (TextField) number.getTextField().get(0);

            number.show();
            state.reset();
            field.setText(decimalText);
            fire(field, KeyCode.ENTER);
            require(state.callbackCount == 1, "value callback count");
            require(state.fxThread, "value callback FX thread");
            require(!state.canceled && Math.abs(state.value - 12.5) < 1e-6,
                    "value commit for " + locale);

            number.show();
            state.reset();
            field.setText(decimalText);
            fire(field, KeyCode.ENTER);
            require(state.callbackCount == 1 && state.canceled, "unchanged value cancellation");

            number.show();
            state.reset();
            field.setText(comma ? "15,000" : "15.000");
            fire(field, KeyCode.ESCAPE);
            require(state.callbackCount == 1 && state.canceled, "ESC cancellation");

            number.show();
            state.reset();
            field.setText(comma ? "16,000" : "16.000");
            fire(field, KeyCode.ENTER);
            require(state.callbackCount == 1 && !state.canceled
                            && Math.abs(state.value - 16.0) < 1e-6,
                    "commit after ESC");

            number.show();
            state.reset();
            double beforeInvalid = number.getMostRecentValue();
            field.clear();
            field.appendText("-");
            number.getMostRecentValue();
            require("-".equals(field.getText()),
                    "getter validated and overwrote partial input");
            fire(field, KeyCode.ENTER);
            require(state.callbackCount == 1 && state.canceled,
                    "invalid partial cancellation count=" + state.callbackCount
                            + " canceled=" + state.canceled + " text=" + field.getText());
            require(Math.abs(number.getMostRecentValue() - beforeInvalid) < 1e-6,
                    "invalid partial changed value");

            number.show();
            state.reset();
            beforeInvalid = number.getMostRecentValue();
            field.clear();
            fire(field, KeyCode.ENTER);
            require(state.callbackCount == 1 && state.canceled, "empty value cancellation");
            require(Math.abs(number.getMostRecentValue() - beforeInvalid) < 1e-6,
                    "empty value changed value");

            number.show();
            number.setValue(20);
            state.reset();
            field.setText("22.5");
            number.show();
            number.setValue(99); // model refresh while typing
            require(field.getText().equals("22.5"), "model refresh overwrote typed text");
            require(number.getMostRecentValue() == 20, "refresh overwrote edit baseline");
            fire(field, KeyCode.ENTER);
            require(!state.canceled && state.value == 22.5, "repeated show canceled a valid edit");

            number.show();
            state.reset();
            field.setText("30.5");
            fire(field, KeyCode.UP);
            require(field.getText().replace(',', '.').equals("31.500"), "arrow did not nudge current typed text");
            fire(field, KeyCode.ESCAPE);
            require(number.getMostRecentValue() == 22.5, "arrow destroyed Escape baseline");

            number.show();
            state.reset();
            field.setText("41,25");
            blur(number);
            require(state.callbackCount == 1 && !state.canceled && state.value == 41.25,
                    "field switch did not commit typed value");
            blur(number);
            require(state.callbackCount == 1, "focus loss submitted duplicate edit");

            number.show();
            state.reset();
            field.setText("48.75");
            number.hide(); // another dimension's commit refreshes the controls
            require(state.callbackCount == 1 && !state.canceled && state.value == 48.75,
                    "control refresh discarded a second edited dimension");
        } finally {
            Locale.setDefault(previous);
        }
    }

    private static void blur(ThreedNumber number) {
        // Calls the exact production focus-loss handler without a desktop Stage.
        try {
            var method = ThreedNumber.class.getDeclaredMethod("finishFocusEdit");
            method.setAccessible(true);
            method.invoke(number);
        } catch (ReflectiveOperationException error) { throw new AssertionError(error); }
    }

    private static double numberValue(ProbeState state, ThreedNumber number) {
        state.fxThread = Platform.isFxApplicationThread();
        state.canceled = number.canceled;
        return state.canceled ? Double.NaN : number.getMostRecentValue();
    }

    private static void fire(TextField field, KeyCode code) {
        field.fireEvent(new KeyEvent(KeyEvent.KEY_PRESSED, "", "", code,
                false, false, false, false));
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }

    private static final class ProbeState {
        private int callbackCount;
        private boolean fxThread;
        private boolean canceled;
        private double value;

        private void reset() {
            callbackCount = 0;
            fxThread = false;
            canceled = false;
            value = Double.NaN;
        }

        private void commit(double value) {
            callbackCount++;
            this.value = value;
        }
    }
}
