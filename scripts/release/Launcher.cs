using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;

// The stable launcher stays outside versioned runtimes so updates never
// replace a running executable. Child processes inherit this console and IO.
class Launcher
{
    static string Quote(string value)
    {
        var text = new StringBuilder("\"");
        int slashes = 0;
        foreach (char character in value)
        {
            if (character == '\\') { slashes++; continue; }
            text.Append('\\', character == '"' ? slashes * 2 + 1 : slashes);
            text.Append(character);
            slashes = 0;
        }
        return text.Append('\\', slashes * 2).Append('"').ToString();
    }

    static int Main(string[] args)
    {
        try
        {
            string root = Path.GetDirectoryName(Path.GetDirectoryName(
                Assembly.GetExecutingAssembly().Location));
            string tag = File.ReadAllText(Path.Combine(root, "current-version")).Trim();
            if (!Regex.IsMatch(tag, @"\Av[0-9]+\.[0-9]+\.[0-9]+\z"))
                throw new Exception("Invalid release pointer; rerun the installer.");
            var start = new ProcessStartInfo {
                FileName = Path.Combine(root, "versions", tag, "runtime", "python.exe"),
                Arguments = "-m meow " + string.Join(" ", args.Select(Quote)),
                UseShellExecute = false
            };
            Console.CancelKeyPress += (sender, e) => { e.Cancel = true; };
            using (var child = Process.Start(start)) {
                child.WaitForExit();
                return child.ExitCode;
            }
        }
        catch (Exception error) {
            Console.Error.WriteLine("MEOW: " + error.Message);
            return 1;
        }
    }
}
