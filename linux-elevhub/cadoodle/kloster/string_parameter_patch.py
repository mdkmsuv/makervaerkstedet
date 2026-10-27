"""Use guarded, ordered commits for native text and fixed-choice parameters."""


def patch_string_parameter(replace_once, generated):
    session = generated / "src/main/java/com/commonwealthrobotics/controls/SelectionSession.java"
    text = session.read_text(encoding="utf-8")
    start = text.index("\tprivate void setUpTextBoxEnterData(")
    end = text.index("\n\tprivate void setUpFileBox(", start)
    text = text[:start] + '''    private void setUpTextBoxEnterData(GridPane gp, int line, String text, Parameter para, int width) {
        TextField editor = com.commonwealthrobotics.kloster.ParameterTextEditor.create(
                para.getStrValue(), stringParameterCommitter(para));
        editor.setMinWidth(width);
        gp.add(editor, 1, line);
    }
''' + text[end:]
    start = text.index("\tprivate void setUpComboBoxParametrics(")
    end = text.index("\n\tprivate CSG getSelectedCSG(String string)", start)
    method = text[start:end]
    anchor = "\t\tBowlerStudio.runLater(() -> {\n\t\t\toptions.getSelectionModel().select(para.getStrValue());"
    method = replace_once(method, anchor, '''        final java.util.function.Consumer<String> commit = stringParameterCommitter(para);
        final String[] accepted = {para.getStrValue()};
        BowlerStudio.runLater(() -> {
            options.getSelectionModel().select(accepted[0]);''')
    a = method.index("\t\t\toptions.setOnAction(event -> {")
    b = method.index("\n\t\t\t});", a) + len("\n\t\t\t});")
    method = method[:a] + '''            options.setOnAction(event -> {
                String value = options.getSelectionModel().getSelectedItem();
                if (options.isDisabled() || value == null || java.util.Objects.equals(value, accepted[0])) return;
                commit.accept(value);
                accepted[0] = value;
            });''' + method[b:]
    text = text[:start] + method + text[end:]
    text = replace_once(text, "Label e = new Label(text);\n\t\t\t\t\t\te.setMinWidth(50);",
            "Label e = new Label(text);\n                        e.setWrapText(true);\n                        e.setMaxWidth(100);\n\t\t\t\t\t\te.setMinWidth(50);")
    session.write_text(text, encoding="utf-8")
