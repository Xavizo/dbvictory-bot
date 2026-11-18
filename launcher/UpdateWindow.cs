using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

internal sealed class UpdateWindow : Form
{
    private readonly bool checkOnly;
    private readonly Label heading = new Label();
    private readonly Label versions = new Label();
    private readonly TextBox message = new TextBox();
    private readonly ProgressBar progress = new ProgressBar();
    private readonly Button close = new Button();
    private readonly Button defer = new Button();
    private string pendingToDefer;
    private bool checking = true;
    private int exitCode = 2;

    [STAThread]
    private static int Main(string[] args)
    {
        if (args.Length > 1 || (args.Length == 1 && args[0] != "--check"))
            return 2;
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        using (var window = new UpdateWindow(args.Length == 1))
        {
            Application.Run(window);
            return window.exitCode;
        }
    }

    private UpdateWindow(bool checkOnly)
    {
        this.checkOnly = checkOnly;
        Text = "Aktualizacja DBVictory";
        Icon = SystemIcons.Application;
        Font = SystemFonts.MessageBoxFont;
        AutoScaleMode = AutoScaleMode.Font;
        ClientSize = new Size(466, 209);
        StartPosition = FormStartPosition.CenterScreen;
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false;
        MinimizeBox = true;

        var layout = new TableLayoutPanel();
        layout.Dock = DockStyle.Fill;
        layout.ColumnCount = 1;
        layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        layout.RowCount = 5;
        layout.Padding = new Padding(19, 15, 19, 12);
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        Controls.Add(layout);

        heading.Font = new Font(Font.FontFamily, Font.Size + 4, FontStyle.Bold);
        heading.AutoSize = true;
        heading.Text = "Sprawdzanie wersji…";
        layout.Controls.Add(heading, 0, 0);
        versions.AutoSize = true;
        versions.Margin = new Padding(3, 5, 3, 4);
        layout.Controls.Add(versions, 0, 1);
        message.Dock = DockStyle.Fill;
        message.Multiline = true;
        message.ReadOnly = true;
        message.BorderStyle = BorderStyle.None;
        message.BackColor = SystemColors.Control;
        message.ScrollBars = ScrollBars.Vertical;
        message.AccessibleName = "Szczegóły aktualizacji";
        layout.Controls.Add(message, 0, 2);
        progress.Dock = DockStyle.Fill;
        progress.Height = 6;
        progress.Style = ProgressBarStyle.Marquee;
        progress.MarqueeAnimationSpeed = 26;
        layout.Controls.Add(progress, 0, 3);
        var buttons = new FlowLayoutPanel();
        buttons.FlowDirection = FlowDirection.LeftToRight;
        buttons.Anchor = AnchorStyles.Right;
        buttons.AutoSize = true;
        buttons.WrapContents = false;
        layout.Controls.Add(buttons, 0, 4);
        defer.Text = "Odłóż wydanie";
        defer.AutoSize = true;
        defer.Visible = false;
        defer.Click += async delegate {
            if (!checking && pendingToDefer != null)
                await UpdateAsync(pendingToDefer);
        };
        buttons.Controls.Add(defer);
        close.Text = "Zamknij";
        close.AutoSize = true;
        close.Anchor = AnchorStyles.Right;
        close.Visible = false;
        close.Click += delegate { Close(); };
        buttons.Controls.Add(close);
    }

    protected override void OnFormClosing(FormClosingEventArgs e)
    {
        base.OnFormClosing(e);
        if (checking && e.CloseReason == CloseReason.UserClosing)
            e.Cancel = true;
    }

    private static bool IsRevision(string value)
    {
        return value != null && Regex.IsMatch(value, @"\A[0-9a-f]{40}\z");
    }

    private static string Revision(IDictionary<string, object> result, string key)
    {
        object value;
        if (!result.TryGetValue(key, out value) || value == null)
            return null;
        var sha = value as string;
        if (!IsRevision(sha))
            throw new InvalidDataException("Odebrano błędny numer wersji.");
        return sha;
    }

    private static IDictionary<string, object> ReadStatus(bool checkOnly, string deferRevision)
    {
        if (deferRevision != null && (checkOnly || !IsRevision(deferRevision)))
            throw new InvalidDataException("Nie można odłożyć wskazanego wydania.");
        string command = deferRevision == null
            ? "preflight" + (checkOnly ? " --check" : "")
            : "defer " + deferRevision;
        var root = Environment.CurrentDirectory;
        var start = new ProcessStartInfo {
            FileName = Path.Combine(root, "venv", "Scripts", "python.exe"),
            Arguments = "-B -X utf8 launcher_files.py " + command,
            WorkingDirectory = root, UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardOutput = true, RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8
        };
        if (!File.Exists(start.FileName) || !File.Exists(Path.Combine(root, "launcher_files.py")))
            throw new FileNotFoundException("W głównej instalacji brakuje Pythona lub pliku launcher_files.py.");
        using (var process = Process.Start(start))
        {
            var output = process.StandardOutput.ReadToEndAsync();
            var error = process.StandardError.ReadToEndAsync();
            process.WaitForExit();
            Task.WaitAll(output, error);
            if (String.IsNullOrWhiteSpace(output.Result))
                throw new InvalidDataException("Aktualizator nie zwrócił wyniku. " + error.Result.Trim());
            var result = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(output.Result);
            if (result == null || !(result.ContainsKey("state") && result["state"] is string
                                  && result.ContainsKey("message") && result["message"] is string))
                throw new InvalidDataException("Aktualizator zwrócił niepełny wynik.");
            string state = (string)result["state"];
            if (process.ExitCode != 0 && (state == "current" || state == "updated"))
                throw new InvalidDataException("Sprawdzanie wersji zakończyło się błędem.");
            return result;
        }
    }

    protected override async void OnShown(EventArgs e)
    {
        base.OnShown(e);
        await UpdateAsync(null);
    }

    private async Task UpdateAsync(string deferRevision)
    {
        checking = true;
        pendingToDefer = null;
        close.Visible = defer.Visible = false;
        AcceptButton = null;
        CancelButton = null;
        progress.Visible = true;
        message.Clear();
        if (deferRevision != null)
            heading.Text = "Odkładanie wydania…";
        try
        {
            var result = await Task.Run(() => ReadStatus(checkOnly, deferRevision));
            var state = (string)result["state"];
            var installed = Revision(result, "installed");
            var pending = Revision(result, "pending");
            var labels = new List<string>();
            if (installed != null) labels.Add("Zainstalowana  " + installed.Substring(0, 7));
            if (pending != null && pending != installed) labels.Add("Dostępna  " + pending.Substring(0, 7));
            versions.Text = String.Join("     ·     ", labels);
            if (state != "current")
                message.Text = (string)result["message"];
            switch (state)
            {
                case "current": heading.Text = deferRevision == null ? "Wersja gotowa" : "Wydanie odłożone"; break;
                case "updated": heading.Text = "Zainstalowano aktualizację"; break;
                case "busy": heading.Text = "Aktualizacja musi poczekać"; break;
                case "available": heading.Text = "Dostępna aktualizacja"; break;
                case "recovery": heading.Text = "Instalacja wymaga naprawy"; break;
                case "error":
                    heading.Text = deferRevision == null ? "Aktualizacja zatrzymana" : "Nie udało się odłożyć wydania";
                    object canDefer;
                    if (!checkOnly && deferRevision == null && installed != null && pending != installed
                            && result.TryGetValue("can_defer", out canDefer) && Object.Equals(canDefer, true))
                        pendingToDefer = pending;
                    break;
                default: throw new InvalidDataException("Nieznany wynik sprawdzania wersji.");
            }
            if (!checkOnly && (state == "current" || state == "updated"))
                exitCode = 0;
        }
        catch (Exception ex)
        {
            heading.Text = deferRevision == null ? "Nie udało się sprawdzić wersji" : "Nie udało się odłożyć wydania";
            message.Text = ex.GetBaseException().Message;
        }
        checking = false;
        progress.Visible = false;
        if (exitCode == 0)
        {
            await Task.Delay(1150);
            if (!IsDisposed) Close();
        }
        else
        {
            defer.Visible = pendingToDefer != null;
            close.Visible = true;
            AcceptButton = close;
            CancelButton = close;
            close.Focus();
        }
    }
}
