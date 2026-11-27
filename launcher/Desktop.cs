using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

// DBVictory.exe (and DBVictory Launcher.exe, its old name, kept for shortcuts and for
// launchers that restart under that name after an update): starts the launcher window
// (launcher/desktop_app.py) with the installation's own Python, as venv\Scripts\DBVictory.exe,
// pythonw with the bot's icon, so Task Manager and the taskbar show the bot.
internal static class Desktop
{
    [STAThread]
    private static int Main(string[] args)
    {
        if (args.Length > 1 || (args.Length == 1 && args[0] != "--check")) return 2;
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string python = Path.Combine(root, @"venv\Scripts\DBVictory.exe");
        if (!File.Exists(python)) python = Path.Combine(root, @"venv\Scripts\pythonw.exe");
        string entry = Path.Combine(root, @"launcher\desktop_app.py");
        try
        {
            if (!File.Exists(python) || !File.Exists(entry) || !File.Exists(Path.Combine(root, "launcher_files.py")))
                throw new FileNotFoundException("Brakuje plików launchera lub lokalnego Pythona. Otwórz launcher z głównej instalacji DBVictory.");
            var start = new ProcessStartInfo(python, "-B -X utf8 -m launcher.desktop_app" + (args.Length == 1 ? " --check" : "")) {
                UseShellExecute = false, WorkingDirectory = root, CreateNoWindow = true
            };
            foreach (string name in new [] {"PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "DBV_DASHBOARD_TOKEN"})
                start.EnvironmentVariables.Remove(name);
            using (Process process = Process.Start(start)) { return process == null ? 1 : 0; }
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "DBVictory Launcher", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
