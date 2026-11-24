const path = require('node:path');
const {execFile} = require('node:child_process');

function createNotificationAudio({fallback, run=execFile, platform=process.platform, directory=__dirname}) {
  let child = null, stopped = false;
  return {
    play(kind) {
      if (stopped || child) return false;
      if (platform !== 'win32') { fallback(); return true; }
      const python = path.resolve(directory, '../../venv/Scripts/pythonw.exe');
      const helper = path.join(directory, 'windows_alert.py');
      child = run(python, ['-I', '-B', helper, ['message', 'vitals'].includes(kind) ? kind : 'player'],
        {windowsHide: true, timeout: 4000}, error => {
          child = null;
          if (error && !stopped) fallback();
        });
      return true;
    },
    stop() { stopped = true; child?.kill(); child = null; },
  };
}
module.exports = {createNotificationAudio};
