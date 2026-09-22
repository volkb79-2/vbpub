import * as vscode from 'vscode';
import { getWebviewHtml } from './html';

export function createWebviewPanel(context: vscode.ExtensionContext, log: vscode.OutputChannel) {
    const panel = vscode.window.createWebviewPanel(
        'speechToCopilot',
        'Speech-to-Copilot',
        { viewColumn: vscode.ViewColumn.Beside, preserveFocus: true },
        {
            enableScripts: true,
            retainContextWhenHidden: true,
            localResourceRoots: [vscode.Uri.joinPath(context.extensionUri, 'media')]
        }
    );

    panel.webview.html = getWebviewHtml();

    panel.webview.onDidReceiveMessage(msg => {
        switch (msg.type) {
            case 'log':
                log.appendLine(`[webview] ${msg.message}`);
                break;
            case 'insertText':
                insertAtCursor(msg.text, log);
                break;
        }
    });

    return panel;
}

async function insertAtCursor(text: string, log: vscode.OutputChannel) {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
        vscode.window.showWarningMessage('No active editor to insert transcription.');
        return;
    }
    await editor.edit(builder => {
        for (const sel of editor.selections) {
            builder.insert(sel.active, text);
        }
    });
    log.appendLine('[insert] Text inserted from transcription');
}
