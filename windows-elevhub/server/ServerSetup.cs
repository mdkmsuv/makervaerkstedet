using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Threading.Tasks;
using System.Windows.Forms;

class ServerSetup : Form {
  readonly Button install = new Button { Text = "Installér server", Width = 190, Height = 44 };
  readonly Button measure = new Button { Text = "Mål serverens behov", Width = 200, Height = 44 };
  readonly TextBox log = new TextBox { Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical, Dock = DockStyle.Fill, BorderStyle = BorderStyle.FixedSingle };
  readonly Label status = new Label { Text = "Klar til installation på den valgte serveradresse", AutoSize = true };
  bool working;
  string Root { get { return AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar); } }

  [STAThread] static void Main() { Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false); Application.Run(new ServerSetup()); }
  ServerSetup() {
    Text = "Elevhub · Server"; Width = 820; Height = 590; MinimumSize = new Size(720, 500);
    StartPosition = FormStartPosition.CenterScreen; BackColor = Color.White; Font = new Font("Arial", 10);
    var layout = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(26), RowCount = 6, ColumnCount = 1 };
    layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 58)); layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 75));
    layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 60)); layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 34));
    layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100)); layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 35));
    layout.Controls.Add(new Label { Text = "Elevhub på serveren", Font = new Font("Arial", 24), AutoSize = true, ForeColor = Color.FromArgb(180, 18, 26) }, 0, 0);
    layout.Controls.Add(new Label { Text = "Køsystem, lærerpanel og værksteder på den konfigurerede serveradresse.\nEfter installationen kan du måle plads, RAM og CPU for 12 elever og 24 som reserve. Testen starter ingen printere.", Dock = DockStyle.Fill }, 0, 1);
    var buttons = new FlowLayoutPanel { Dock = DockStyle.Fill };
    foreach (var button in new[] { install, measure }) { button.FlatStyle = FlatStyle.Flat; button.BackColor = Color.FromArgb(180,18,26); button.ForeColor = Color.White; buttons.Controls.Add(button); }
    layout.Controls.Add(buttons,0,2); layout.Controls.Add(status,0,3); layout.Controls.Add(log,0,4);
    layout.Controls.Add(new Label { Text = "Programmer og elevdata holdes adskilt. Afinstallation bevarer data.", AutoSize = true },0,5);
    Controls.Add(layout);
    install.Click += async (s,e) => await Install();
    measure.Click += async (s,e) => await Measure();
    FormClosing += (s,e) => { if (working) { e.Cancel = true; MessageBox.Show(this,"Vent til den igangværende handling er færdig.","Elevhub"); } };
  }
  void Busy(bool value, string text) { working = value; install.Enabled = measure.Enabled = !value; status.Text = text; }
  void Add(string text) { if (!String.IsNullOrWhiteSpace(text)) log.AppendText(text + Environment.NewLine); }
  static string Quote(string value) { if (value.Contains("\"")) throw new ArgumentException("Invalid path"); return "\"" + value + "\""; }
  async Task<int> Run(string executable, string arguments) {
    var info = new ProcessStartInfo(executable, arguments) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true, WorkingDirectory = Root };
    using (var process = Process.Start(info)) {
      var output = process.StandardOutput.ReadToEndAsync(); var errors = process.StandardError.ReadToEndAsync();
      await Task.Run(() => process.WaitForExit()); Add(await output); Add(await errors); return process.ExitCode;
    }
  }
  async Task Install() {
    Busy(true, "Installerer serveren. Det kan tage nogle minutter …");
    try {
      string powershell = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "WindowsPowerShell\\v1.0\\powershell.exe");
      int code = await Run(powershell, "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File " + Quote(Path.Combine(Root,"server\\install.ps1")) + " -PackageRoot " + Quote(Root));
      Busy(false, code == 0 ? "Serveren er installeret og startet." : "Installationen blev ikke gennemført. Se beskeden nedenfor.");
    } catch (Exception error) { Add(error.Message); Busy(false,"Installationen blev afbrudt."); }
  }
  async Task Measure() {
    string installed = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),"ElevhubServer");
    string data = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),"ElevhubServer");
    if (!File.Exists(Path.Combine(installed,"server\\benchmark.py"))) { MessageBox.Show(this,"Installér serveren, før målingen startes.","Elevhub"); return; }
    Busy(true,"Måler 12 og 24 elever, 3D-klargøring og AI. Vent på rapporten …");
    try {
      int code = await Run(Path.Combine(installed,"runtime\\python.exe"), Quote(Path.Combine(installed,"server\\benchmark.py")) + " --install-root " + Quote(installed) + " --data-root " + Quote(data));
      Busy(false,code == 0 ? "Målingen er færdig. Rapporten ligger i serverens benchmarkmappe." : "Målingen kunne ikke afsluttes. Se beskeden nedenfor.");
    } catch (Exception error) { Add(error.Message); Busy(false,"Målingen blev afbrudt."); }
  }
}
