import * as vscode from 'vscode';
import WebSocket from 'ws';
import { createWebviewPanel } from './webview/panel';
import { TranscriptionAggregator } from './services/transcriptionAggregator';

// Extension state
let wsConnection: WebSocket | null = null;
let isRecording = false;
let currentPanel: vscode.WebviewPanel | null = null;

// Simple in-memory aggregator (mirrors backend incremental behaviour for future local fallback)
const aggregator = new TranscriptionAggregator();

export function activate(context: vscode.ExtensionContext) {
    const output = vscode.window.createOutputChannel('Speech-to-Copilot');
    output.appendLine('[activate] Extension starting');

    // Helper function to get API URL from configuration
    const getApiUrl = () => {
        const config = vscode.workspace.getConfiguration('speechToCopilot');
        return config.get<string>('apiUrl', 'http://localhost:8000');
    };

    // Helper function to insert text at cursor or selection
    const insertText = async (text: string) => {
        const editor = vscode.window.activeTextEditor;
        if (!editor) {
            output.appendLine('[insertText] No active editor');
            return;
        }

        const insertMode = vscode.workspace.getConfiguration('speechToCopilot').get<string>('insertMode', 'cursor');
        
        await editor.edit((editBuilder: vscode.TextEditorEdit) => {
            if (insertMode === 'selection' && !editor.selection.isEmpty) {
                // Replace selected text
                editBuilder.replace(editor.selection, text);
                output.appendLine(`[insertText] Replaced selection with: "${text}"`);
            } else if (insertMode === 'comment') {
                // Insert as comment
                const commentText = `// ${text}\n`;
                editBuilder.insert(editor.selection.active, commentText);
                output.appendLine(`[insertText] Inserted as comment: "${commentText}"`);
            } else {
                // Insert at cursor (default)
                editBuilder.insert(editor.selection.active, text);
                output.appendLine(`[insertText] Inserted at cursor: "${text}"`);
            }
        });
    };

    // Helper function to connect to WebSocket API
    const connectWebSocket = () => {
        if (wsConnection && wsConnection.readyState === WebSocket.OPEN) {
            return; // Already connected
        }

        const apiUrl = getApiUrl().replace('http://', 'ws://').replace('https://', 'wss://');
        const wsUrl = `${apiUrl}/ws/audio`;
        
        output.appendLine(`[WebSocket] Connecting to: ${wsUrl}`);
        wsConnection = new WebSocket(wsUrl);
        const ws = wsConnection; // Capture reference for type safety

        ws.on('open', () => {
            output.appendLine('[WebSocket] Connected successfully');
            if (currentPanel) {
                currentPanel.webview.postMessage({ type: 'websocket_connected' });
            }
        });

        ws.on('message', async (data: WebSocket.Data) => {
            try {
                const message = JSON.parse(data.toString());
                output.appendLine(`[WebSocket] Received: ${JSON.stringify(message)}`);

                if (message.type === 'transcription_result') {
                    const incremental = message.data?.incremental || '';
                    const context = message.data?.context || '';
                    
                    // Insert incremental text into editor
                    if (incremental && incremental.trim()) {
                        await insertText(incremental);
                    }

                    // Send update to webview panel
                    if (currentPanel) {
                        currentPanel.webview.postMessage({
                            type: 'transcription_update',
                            data: {
                                incremental,
                                fullText: message.data?.text || '',
                                context,
                                chunkCount: message.data?.chunk_count || 0
                            }
                        });
                    }
                }
            } catch (error) {
                output.appendLine(`[WebSocket] Parse error: ${error}`);
            }
        });

        ws.on('error', (error: Error) => {
            output.appendLine(`[WebSocket] Error: ${error}`);
            vscode.window.showErrorMessage(`WebSocket connection failed: ${error.message}`);
        });

        ws.on('close', () => {
            output.appendLine('[WebSocket] Connection closed');
            wsConnection = null;
            if (currentPanel) {
                currentPanel.webview.postMessage({ type: 'websocket_disconnected' });
            }
        });
    };

    // Command: Start Recording (opens webview panel and connects WebSocket)
    const startDisposable = vscode.commands.registerCommand('speechToCopilot.startRecording', async () => {
        output.appendLine('[command] startRecording invoked');
        
        if (isRecording) {
            vscode.window.showWarningMessage('Recording is already active');
            return;
        }

        // Connect to WebSocket API
        connectWebSocket();
        
        // Create or reveal webview panel
        if (!currentPanel) {
            currentPanel = createWebviewPanel(context, output);
            
            // Handle panel disposal
            currentPanel.onDidDispose(() => {
                output.appendLine('[webview] Panel disposed');
                currentPanel = null;
            });
        }
        
        currentPanel.reveal(vscode.ViewColumn.Beside);
        isRecording = true;
        
        // Set context for when clause in package.json
        vscode.commands.executeCommand('setContext', 'speechToCopilot.isRecording', true);
    });

    // Command: Stop Recording
    const stopDisposable = vscode.commands.registerCommand('speechToCopilot.stopRecording', async () => {
        output.appendLine('[command] stopRecording invoked');
        
        isRecording = false;
        vscode.commands.executeCommand('setContext', 'speechToCopilot.isRecording', false);
        
        // Send stop message via WebSocket
        if (wsConnection && wsConnection.readyState === WebSocket.OPEN) {
            wsConnection.send(JSON.stringify({ type: 'stop_recording' }));
        }
        
        vscode.window.showInformationMessage('Recording stopped');
    });

    // Command: Insert last transcription
    const insertLastDisposable = vscode.commands.registerCommand('speechToCopilot.insertLastTranscription', async () => {
        const lastText = aggregator.getLastTranscription();
        if (lastText) {
            await insertText(lastText);
            output.appendLine(`[command] Inserted last transcription: "${lastText}"`);
        } else {
            vscode.window.showInformationMessage('No transcription available');
        }
    });

    // Command: Scan repository (simplified implementation)
    const scanDisposable = vscode.commands.registerCommand('speechToCopilot.scanRepository', async () => {
        output.appendLine('[command] scanRepository invoked');
        
        // For now, just show a placeholder message
        const workspace = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || 'No workspace';
        vscode.window.showInformationMessage(`Repository scan requested for: ${workspace}`);
        output.appendLine(`[scanner] Scan requested for workspace: ${workspace}`);
    });

    context.subscriptions.push(
        startDisposable, 
        stopDisposable, 
        insertLastDisposable,
        scanDisposable, 
        output
    );
}

export function deactivate() {
    // Nothing yet – rely on VSCode process lifecycle
}
