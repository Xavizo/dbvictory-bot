const path = require('node:path');
const {execFile} = require('node:child_process');

function createNotificationAudio({fallback, run=execFile, platform=process.platform, directory=__dirname}) {
  let child = null, stopped = false;
  function interrupt() {
    const playing = child;
    child = null;
    playing?.kill();
  }
  return {
    interrupt,
    play(kind) {
      if (stopped || child) return false;
      if (platform !== 'win32') { fallback(); return true; }
      const python = path.resolve(directory, '../../venv/Scripts/pythonw.exe');
      const helper = path.join(directory, 'windows_alert.py');
      const playing = run(python, ['-I', '-B', helper, ['message', 'vitals'].includes(kind) ? kind : 'player'],
        {windowsHide: true, timeout: 4000}, error => {
          if (child !== playing) return;
          child = null;
          if (error && !stopped) fallback();
        });
      child = playing;
      return true;
    },
    stop() { stopped = true; interrupt(); },
  };
}
module.exports = {createNotificationAudio};
