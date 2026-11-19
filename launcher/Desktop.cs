using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class Desktop
{
    // First upgrade: present the same updater even before the queued UI has been installed.
    private static string BootstrapEntry()
    {
        var assembly = typeof(Desktop).Assembly;
        string cache = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "DBVictory Launcher", "bootstrap");
        string release = Path.Combine(cache, assembly.ManifestModule.ModuleVersionId.ToString("N"));
        string entry = Path.Combine(release, "main.js");
        if (File.Exists(entry)) return entry;
        Directory.CreateDirectory(cache);
        string temp = Path.Combine(cache, Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(temp);
        try
        {
            foreach (string name in new [] {"main.js","bridge.js","host.js","index.html","effects.js","render.js","style.css","hero.webp"})
            {
                using (var src = assembly.GetManifestResourceStream("DBVictory." + name))
                using (var dst = File.Create(Path.Combine(temp, name))) src.CopyTo(dst);
            }
            try { Directory.Move(temp, release); }
            catch (IOException) { if (!File.Exists(entry)) throw; }
        }
        finally { if (Directory.Exists(temp)) Directory.Delete(temp, true); }
        return entry;
    }
    [STAThread]
    private static int Main(string[] args)
    {
        if (args.Length > 1 || (args.Length == 1 && args[0] != "--check")) return 2;
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string runtime = Path.Combine(root, @"dashboard\node_modules\electron\dist\electron.exe");
        string entry = Path.Combine(root, @"launcher\desktop\main.js");
        try
        {
            if (!File.Exists(runtime) || !File.Exists(Path.Combine(root,"launcher_files.py")))
                throw new FileNotFoundException("Brakuje plików launchera lub lokalnego środowiska Electron. Otwórz launcher z głównej instalacji DBVictory.");
            if (!File.Exists(entry)) entry = BootstrapEntry();
            var start = new ProcessStartInfo(runtime, "\"" + entry + "\"" + (args.Length == 1 ? " --check" : "")) {
                UseShellExecute = false, WorkingDirectory = root, CreateNoWindow = true
            };
            start.EnvironmentVariables.Remove("ELECTRON_RUN_AS_NODE");
            start.EnvironmentVariables.Remove("NODE_OPTIONS");
            using (Process process = Process.Start(start)) { return process == null ? 1 : 0; }
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "DBVictory Launcher", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
