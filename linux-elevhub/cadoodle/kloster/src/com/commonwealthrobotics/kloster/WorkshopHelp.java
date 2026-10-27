package com.commonwealthrobotics.kloster;

import javafx.scene.control.*;
import javafx.scene.layout.*;

/** Short, offline instructions available without a hover or keyboard shortcut. */
final class WorkshopHelp {
    private WorkshopHelp() {}

    static VBox content() {
        VBox content = new VBox(14);
        content.getStyleClass().add("kloster-help-content");
        content.getChildren().addAll(
            section("cube", "1. Vælg en figur", "Tryk på en figur til højre. Tryk på pladen for at sætte den."),
            section("cube", "Markér og fravælg", "Tryk på en figur på pladen for at markere den. Tryk på den igen for at fjerne markeringen."),
            section("fit", "2. Gør figuren til din", "Træk i figuren for at flytte den. Træk i de firkantede håndtag for at ændre størrelse. Du kan også trykke på et mål og skrive et tal."),
            section("undo", "Du kan altid prøve igen", "Brug Fortryd, hvis noget blev forkert. Gentag henter ændringen frem igen."),
            section("plus", "Se modellen tæt på", "Spred to fingre for at zoome ind. Saml dem for at zoome ud. Du kan også bruge + og − til venstre."),
            section("fit", "Drej visningen med én finger", "Træk med én finger på den tomme plade. Træk på en figur eller dens håndtag, når du vil ændre selve figuren."),
            section("home", "Find tilbage til pladen", "Tryk på huset til venstre for at vende tilbage til startvisningen."),
            section("group", "Byg med flere figurer", "Tryk Vælg flere, og tryk på de figurer, du vil markere. Tryk på en figur igen for at fjerne markeringen. Brug Gruppér til at samle dem, eller Opdel til at skille dem ad."),
            section("print", "Når modellen er klar", "Giv projektet et navn øverst. Brug Send til print, når du vil sende din model til printerkøen.")
        );
        return content;
    }

    private static HBox section(String icon, String title, String detail) {
        Label heading = new Label(title);
        heading.getStyleClass().add("kloster-help-heading");
        Label description = new Label(detail);
        description.setWrapText(true);
        description.setMaxWidth(Double.MAX_VALUE);
        VBox text = new VBox(4, heading, description);
        text.setMinWidth(0);
        HBox.setHgrow(text, Priority.ALWAYS);
        return new HBox(12, WorkshopIcons.create(icon), text);
    }

    static void show(Pane root) {
        Dialog<Void> dialog = new Dialog<>();
        dialog.setTitle("Hjælp til 3D-værkstedet");
        if (root.getScene() != null && root.getScene().getWindow() != null)
            dialog.initOwner(root.getScene().getWindow());
        dialog.setResizable(true);
        DialogPane pane = dialog.getDialogPane();
        pane.getStyleClass().addAll("kloster-dialog", "kloster-help");
        pane.getStylesheets().add(WorkshopHelp.class.getResource("/com/commonwealthrobotics/stylesheet.css").toExternalForm());
        pane.setPrefSize(600, 580);
        ScrollPane scroll = new ScrollPane(content());
        scroll.setFitToWidth(true);
        scroll.setPannable(true);
        scroll.setHbarPolicy(ScrollPane.ScrollBarPolicy.NEVER);
        pane.setContent(scroll);
        pane.getButtonTypes().add(new ButtonType("Byg videre", ButtonBar.ButtonData.CANCEL_CLOSE));
        dialog.show();
    }
}
