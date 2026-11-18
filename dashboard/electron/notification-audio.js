const path=require('node:path');
const {execFile}=require('node:child_process');

// Fixed bundled WAV files only. No command text or paths from IPC or chat.
function createNotificationAudio({fallback,run=execFile,platform=process.platform,systemRoot=process.env.SystemRoot||'C:\\Windows',directory=__dirname}) {
  let child=null,stopped=false;
  return {
    play(kind){
      if(stopped||child)return false;
      if(platform!=='win32'){fallback();return true;}
      const file=path.join(directory,'sounds',kind==='message'?'message.wav':'player.wav');
      const script="$ErrorActionPreference='Stop'; $sound=New-Object System.Media.SoundPlayer; $sound.SoundLocation='"+
        file.replace(/'/g,"''")+"'; try { $sound.Load(); $sound.PlaySync() } finally { $sound.Dispose() }";
      child=run(path.join(systemRoot,'System32','WindowsPowerShell','v1.0','powershell.exe'),
        ['-NoProfile','-NonInteractive','-WindowStyle','Hidden','-EncodedCommand',Buffer.from(script,'utf16le').toString('base64')],
        {windowsHide:true,timeout:4000},error=>{child=null;if(error&&!stopped)fallback();});
      return true;
    },
    stop(){stopped=true;child?.kill();child=null;},
  };
}
module.exports={createNotificationAudio};
