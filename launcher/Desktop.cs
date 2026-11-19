using System;
using System.Diagnostics;
using System.Collections.Generic;
using System.IO;
using System.Web.Script.Serialization;
using System.Windows.Forms;

internal static class Desktop
{
    private static Version NotesVersion(Stream notes)
    {
        using (var reader = new StreamReader(notes))
        {
            var releases = new JavaScriptSerializer().Deserialize<Dictionary<string, object>[]>(reader.ReadToEnd());
            object value;
            Version version;
            if (releases != null && releases.Length > 0 && releases[0].TryGetValue("version", out value)
                && Version.TryParse(Convert.ToString(value).TrimStart('v'), out version)) return version;
            return new Version();
        }
    }

    private static bool UseBootstrap(string root, string entry)
    {
        string notes = Path.Combine(root, @"launcher\desktop\release-notes.json");
        if (!File.Exists(entry) || !File.Exists(notes)) return true;
        if (!File.Exists(Path.Combine(root, @".codex\updates\pending.json"))) return false;
        // Use the updated Start button in the same window that installs the queued release.
        try
        {
            return NotesVersion(typeof(Desktop).Assembly.GetManifestResourceStream("DBVictory.release-notes.json"))
                > NotesVersion(File.OpenRead(notes));
        }
        catch (ArgumentException) { return false; }
        catch (InvalidOperationException) { return false; }
        catch (IOException) { return false; }
    }

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
            foreach (string name in new [] {"main.js","bridge.js","host.js","index.html","effects.js","render.js","style.css","hero.webp","release-notes.json"})
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
            if (UseBootstrap(root, entry)) entry = BootstrapEntry();
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
