export function getWebviewHtml(): string {
    // Keeping inline minimal UI – real UI is in SvelteKit frontend; this acts as lightweight bridge.
    return /* html */ `<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Speech-to-Copilot</title>
    <style>
      body { font-family: system-ui, sans-serif; margin: 0; padding: 1rem; background:#111; color:#eee; }
      .row { display:flex; gap:.5rem; margin-bottom: .75rem; }
      button { background:#4b6ef6; color:#fff; border:none; padding:.6rem 1rem; border-radius:6px; cursor:pointer; }
      button:hover { background:#3658c7; }
      #status { font-size:.75rem; opacity:.8; }
      #log { font-family: monospace; background:#222; padding:.5rem; height:120px; overflow:auto; border:1px solid #333; }
      #transcript { min-height:80px; border:1px solid #333; padding:.5rem; background:#181818; }
    </style>
  </head>
  <body>
    <h2>Speech-to-Copilot (Embedded Panel)</h2>
    <div id="status">Idle</div>
    <div class="row">
      <button id="startBtn">Start (Simulated)</button>
      <button id="stopBtn">Stop</button>
      <button id="insertBtn">Insert Latest</button>
    </div>
    <div id="transcript" placeholder="Transcript will appear here..."></div>
    <h4>Log</h4>
    <div id="log"></div>
    <script>
      const vscode = acquireVsCodeApi();
      let recording = false;
      let simulatedText = '';

      function log(msg){
        document.getElementById('log').innerHTML += msg + '<br/>';
        vscode.postMessage({ type:'log', message: msg });
      }

      document.getElementById('startBtn').onclick = () => {
        if(recording) return;
        recording = true;
        simulatedText = '';
        document.getElementById('status').textContent = 'Recording (simulated)...';
        log('Simulated recording started');
        simulateStream();
      };

      document.getElementById('stopBtn').onclick = () => {
        recording = false;
        document.getElementById('status').textContent = 'Stopped';
        log('Recording stopped');
      };

      document.getElementById('insertBtn').onclick = () => {
        if(!simulatedText){
          log('No transcript yet to insert');
          return;
        }
        vscode.postMessage({ type:'insertText', text: simulatedText + '\n' });
        log('Inserted transcript to editor');
      };

      function simulateStream(){
        if(!recording) return;
        const tokens = ['function',' generate',' AdaptiveCadence','()',' {',' return',' 42',';',' }'];
        const next = tokens[Math.floor(Math.random()*tokens.length)];
        simulatedText += next;
        document.getElementById('transcript').textContent = simulatedText;
        log('Token: '+ next.trim());
        setTimeout(simulateStream, 600);
      }
    </script>
  </body>
</html>`;
}
