<!-- Main Speech-to-Copilot Interface -->
<script>
	import { onMount, onDestroy } from 'svelte';
	import { audioRecorder, isRecording, audioLevel, recordingDuration, audioError } from '$lib/audio-recorder.js';
	import { wsClient, isConnected, latestTranscription, transcriptionResults, latestContext } from '$lib/websocket-client.js';
	
	// Component state
	let recordingMode = 'batch'; // 'batch' or 'streaming'
	let showSettings = false;
	let showResults = false;
	let isProcessing = false;
	
	// Settings
	let enableContext = true;
	let enableEnhancement = true;
	let processingIntent = 'code';
	
	// Audio visualization
	let audioVisualizerCanvas;
	let visualizerContext;
	let animationFrame;
	
	// Format duration for display
	$: formattedDuration = formatDuration($recordingDuration);
	
	// Format audio level as percentage
	$: audioLevelPercent = Math.round($audioLevel * 100);
	
	onMount(() => {
		// Setup audio recorder callbacks
		audioRecorder.onDataAvailable = handleAudioChunk;
		audioRecorder.onRecordingComplete = handleRecordingComplete;
		audioRecorder.onError = handleAudioError;
		
		// Setup WebSocket callbacks
		wsClient.onTranscriptionResult = handleTranscriptionResult;
		wsClient.onError = handleWebSocketError;
		
		// Update WebSocket configuration
		wsClient.updateConfig({
			enableContext,
			enableEnhancement
		});
		
		// Initialize audio visualizer
		initializeVisualizer();
	});
	
	onDestroy(() => {
		// Cleanup
		audioRecorder.cleanup();
		
		if (animationFrame) {
			cancelAnimationFrame(animationFrame);
		}
	});
	
	/**
	 * Initialize audio visualizer
	 */
	function initializeVisualizer() {
		if (audioVisualizerCanvas) {
			visualizerContext = audioVisualizerCanvas.getContext('2d');
			audioVisualizerCanvas.width = 200;
			audioVisualizerCanvas.height = 60;
		}
	}
	
	/**
	 * Start recording
	 */
	async function startRecording() {
		try {
			isProcessing = true;
			
			const success = await audioRecorder.startRecording();
			if (success) {
				startVisualizer();
			}
			
		} catch (error) {
			console.error('Failed to start recording:', error);
		} finally {
			isProcessing = false;
		}
	}
	
	/**
	 * Stop recording
	 */
	function stopRecording() {
		audioRecorder.stopRecording();
		stopVisualizer();
	}
	
	/**
	 * Handle audio chunk (for streaming)
	 */
	async function handleAudioChunk(audioData) {
		if (recordingMode === 'streaming' && $isConnected) {
			await wsClient.sendAudioChunk(audioData);
		}
	}
	
	/**
	 * Handle recording complete (for batch processing)
	 */
	async function handleRecordingComplete(audioData) {
		console.log('Recording completed:', audioData);
		
		if (recordingMode === 'batch') {
			isProcessing = true;
			
			try {
				const result = await wsClient.sendAudioBatch(audioData);
				if (result) {
					showResults = true;
				}
			} catch (error) {
				console.error('Failed to process audio:', error);
			} finally {
				isProcessing = false;
			}
		}
	}
	
	/**
	 * Handle transcription result
	 */
	function handleTranscriptionResult(result) {
		console.log('New transcription result:', result);
		showResults = true;
	}
	
	/**
	 * Handle audio errors
	 */
	function handleAudioError(error) {
		console.error('Audio error:', error);
		isProcessing = false;
	}
	
	/**
	 * Handle WebSocket errors
	 */
	function handleWebSocketError(error) {
		console.error('WebSocket error:', error);
		isProcessing = false;
	}
	
	/**
	 * Start audio visualizer
	 */
	function startVisualizer() {
		if (!visualizerContext) return;
		
		const draw = () => {
			// Clear canvas
			visualizerContext.clearRect(0, 0, 200, 60);
			
			// Draw waveform visualization
			visualizerContext.fillStyle = '#667eea';
			visualizerContext.globalAlpha = 0.8;
			
			const barWidth = 4;
			const barSpacing = 2;
			const maxBars = Math.floor(200 / (barWidth + barSpacing));
			
			for (let i = 0; i < maxBars; i++) {
				const height = ($audioLevel * 60) * (Math.random() * 0.5 + 0.5);
				const x = i * (barWidth + barSpacing);
				const y = (60 - height) / 2;
				
				visualizerContext.fillRect(x, y, barWidth, height);
			}
			
			if ($isRecording) {
				animationFrame = requestAnimationFrame(draw);
			}
		};
		
		draw();
	}
	
	/**
	 * Stop audio visualizer
	 */
	function stopVisualizer() {
		if (animationFrame) {
			cancelAnimationFrame(animationFrame);
			animationFrame = null;
		}
		
		if (visualizerContext) {
			visualizerContext.clearRect(0, 0, 200, 60);
		}
	}
	
	/**
	 * Format duration in MM:SS
	 */
	function formatDuration(seconds) {
		const mins = Math.floor(seconds / 60);
		const secs = Math.floor(seconds % 60);
		return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
	}
	
	/**
	 * Copy text to clipboard
	 */
	async function copyToClipboard(text) {
		try {
			await navigator.clipboard.writeText(text);
		} catch (error) {
			console.error('Failed to copy to clipboard:', error);
		}
	}
	
	/**
	 * Clear all results
	 */
	function clearResults() {
		wsClient.clearResults();
		showResults = false;
	}
	
	/**
	 * Update WebSocket config when settings change
	 */
	$: {
		wsClient.updateConfig({
			enableContext,
			enableEnhancement
		});
	}
</script>

<svelte:head>
	<title>Speech-to-Copilot - Voice Coding Assistant</title>
</svelte:head>

<div class="space-y-8">
	<!-- Hero Section -->
	<div class="text-center">
		<h2 class="text-3xl font-bold text-white mb-4">
			Enhanced Voice Coding with AI
		</h2>
		<p class="text-xl text-gray-300 max-w-3xl mx-auto">
			Transform your speech into clean, context-aware code using advanced speech recognition 
			and repository-aware post-processing.
		</p>
	</div>
	
	<!-- Recording Interface -->
	<div class="bg-gray-800/50 backdrop-blur-sm rounded-2xl p-8 border border-gray-700">
		<div class="max-w-2xl mx-auto">
			{#if $latestContext}
				<div class="mb-4 flex flex-wrap gap-2">
					{#each $latestContext.split(/\s+/).slice(0,8) as token}
						<span class="px-2 py-1 rounded-md text-xs font-mono bg-gray-700/60 border border-gray-600 text-gray-200">{token}</span>
					{/each}
					<span class="text-[10px] uppercase tracking-wide text-gray-500 self-center">context</span>
				</div>
			{/if}
			<!-- Recording Mode Selector -->
			<div class="flex justify-center mb-8">
				<div class="bg-gray-700/50 rounded-lg p-1 flex">
					<button 
						class="px-4 py-2 rounded-md text-sm font-medium transition-all {recordingMode === 'batch' ? 'bg-primary text-white' : 'text-gray-300 hover:text-white'}"
						on:click={() => recordingMode = 'batch'}
					>
						<i class="fas fa-stop-circle mr-2"></i>
						Batch Mode
					</button>
					<button 
						class="px-4 py-2 rounded-md text-sm font-medium transition-all {recordingMode === 'streaming' ? 'bg-primary text-white' : 'text-gray-300 hover:text-white'}"
						on:click={() => recordingMode = 'streaming'}
					>
						<i class="fas fa-broadcast-tower mr-2"></i>
						Live Streaming
					</button>
				</div>
			</div>
			
			<!-- Audio Visualizer -->
			<div class="flex justify-center mb-6">
				<div class="bg-gray-900/50 rounded-lg p-4 border border-gray-600">
					<canvas 
						bind:this={audioVisualizerCanvas}
						class="block"
						width="200" 
						height="60"
					></canvas>
				</div>
			</div>
			
			<!-- Recording Stats -->
			{#if $isRecording}
				<div class="text-center mb-6 space-y-2">
					<div class="text-2xl font-mono text-white">
						{formattedDuration}
					</div>
					<div class="text-sm text-gray-400">
						Audio Level: {audioLevelPercent}%
					</div>
				</div>
			{/if}
			
			<!-- Recording Controls -->
			<div class="flex justify-center space-x-4">
				{#if !$isRecording}
					<button
						on:click={startRecording}
						disabled={isProcessing || !$isConnected}
						class="bg-gradient-to-r from-primary to-secondary hover:from-primary/80 hover:to-secondary/80 disabled:opacity-50 disabled:cursor-not-allowed text-white px-8 py-3 rounded-xl font-medium transition-all flex items-center space-x-2 shadow-lg"
					>
						{#if isProcessing}
							<i class="fas fa-spinner fa-spin"></i>
							<span>Processing...</span>
						{:else}
							<i class="fas fa-microphone"></i>
							<span>Start Recording</span>
						{/if}
					</button>
				{:else}
					<button
						on:click={stopRecording}
						class="bg-red-600 hover:bg-red-700 text-white px-8 py-3 rounded-xl font-medium transition-all flex items-center space-x-2 shadow-lg"
					>
						<i class="fas fa-stop"></i>
						<span>Stop Recording</span>
					</button>
				{/if}
				
				<button
					on:click={() => showSettings = true}
					class="bg-gray-700 hover:bg-gray-600 text-white px-4 py-3 rounded-xl transition-all"
					title="Settings"
				>
					<i class="fas fa-cog"></i>
				</button>
			</div>
			
			<!-- Connection Warning -->
			{#if !$isConnected}
				<div class="mt-4 p-4 bg-yellow-900/50 border border-yellow-600 rounded-lg">
					<div class="flex items-center space-x-2 text-yellow-200">
						<i class="fas fa-exclamation-triangle"></i>
						<span>Not connected to processing server</span>
					</div>
				</div>
			{/if}
			
			<!-- Audio Error -->
			{#if $audioError}
				<div class="mt-4 p-4 bg-red-900/50 border border-red-600 rounded-lg">
					<div class="flex items-center space-x-2 text-red-200">
						<i class="fas fa-exclamation-circle"></i>
						<span>{$audioError}</span>
					</div>
				</div>
			{/if}
		</div>
	</div>
	
	<!-- Latest Transcription -->
	{#if $latestTranscription}
		<div class="bg-gray-800/50 backdrop-blur-sm rounded-2xl p-6 border border-gray-700">
			<div class="flex items-center justify-between mb-4">
				<h3 class="text-lg font-semibold text-white flex items-center">
					<i class="fas fa-comment-dots mr-2"></i>
					Latest Transcription
				</h3>
				
				<div class="flex space-x-2">
					<button
						on:click={() => copyToClipboard($latestTranscription)}
						class="text-gray-400 hover:text-white p-2 rounded-lg hover:bg-gray-700 transition-all"
						title="Copy to clipboard"
					>
						<i class="fas fa-copy"></i>
					</button>
					
					<button
						on:click={() => showResults = true}
						class="text-gray-400 hover:text-white p-2 rounded-lg hover:bg-gray-700 transition-all"
						title="View all results"
					>
						<i class="fas fa-list"></i>
					</button>
				</div>
			</div>
			
			<div class="bg-gray-900/50 rounded-lg p-4 border border-gray-600">
				<pre class="text-gray-200 whitespace-pre-wrap font-mono text-sm">{$latestTranscription}</pre>
			</div>
		</div>
	{/if}
	
	<!-- Quick Actions -->
	<div class="grid grid-cols-1 md:grid-cols-3 gap-4">
		<button
			on:click={() => showResults = true}
			class="bg-gray-800/50 hover:bg-gray-700/50 border border-gray-700 rounded-xl p-4 transition-all text-left"
		>
			<div class="flex items-center space-x-3">
				<div class="w-10 h-10 bg-blue-600 rounded-lg flex items-center justify-center">
					<i class="fas fa-history text-white"></i>
				</div>
				<div>
					<h4 class="font-medium text-white">View Results</h4>
					<p class="text-sm text-gray-400">{$transcriptionResults.length} transcriptions</p>
				</div>
			</div>
		</button>
		
		<a 
			href="http://localhost:8000/docs" 
			target="_blank"
			class="bg-gray-800/50 hover:bg-gray-700/50 border border-gray-700 rounded-xl p-4 transition-all text-left block"
		>
			<div class="flex items-center space-x-3">
				<div class="w-10 h-10 bg-green-600 rounded-lg flex items-center justify-center">
					<i class="fas fa-code text-white"></i>
				</div>
				<div>
					<h4 class="font-medium text-white">API Documentation</h4>
					<p class="text-sm text-gray-400">Interactive API explorer</p>
				</div>
			</div>
		</a>
		
		<a 
			href="http://localhost:8080" 
			target="_blank"
			class="bg-gray-800/50 hover:bg-gray-700/50 border border-gray-700 rounded-xl p-4 transition-all text-left block"
		>
			<div class="flex items-center space-x-3">
				<div class="w-10 h-10 bg-purple-600 rounded-lg flex items-center justify-center">
					<i class="fas fa-tachometer-alt text-white"></i>
				</div>
				<div>
					<h4 class="font-medium text-white">Dev Dashboard</h4>
					<p class="text-sm text-gray-400">System monitoring</p>
				</div>
			</div>
		</a>
	</div>
</div>

<!-- Settings Modal -->
{#if showSettings}
	<div class="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
		<div class="bg-gray-800 rounded-2xl p-6 max-w-md w-full border border-gray-700">
			<div class="flex items-center justify-between mb-6">
				<h3 class="text-lg font-semibold text-white">Settings</h3>
				<button
					on:click={() => showSettings = false}
					class="text-gray-400 hover:text-white p-1 rounded"
				>
					<i class="fas fa-times"></i>
				</button>
			</div>
			
			<div class="space-y-4">
				<div>
					<label class="flex items-center space-x-3">
						<input
							type="checkbox"
							bind:checked={enableContext}
							class="w-4 h-4 text-primary bg-gray-700 border-gray-600 rounded focus:ring-primary"
						>
						<span class="text-white">Enable Repository Context</span>
					</label>
					<p class="text-sm text-gray-400 ml-7">Use codebase context for better accuracy</p>
				</div>
				
				<div>
					<label class="flex items-center space-x-3">
						<input
							type="checkbox"
							bind:checked={enableEnhancement}
							class="w-4 h-4 text-primary bg-gray-700 border-gray-600 rounded focus:ring-primary"
						>
						<span class="text-white">Enable LLM Enhancement</span>
					</label>
					<p class="text-sm text-gray-400 ml-7">Post-process text for better quality</p>
				</div>
				
				<div>
					<label class="block text-white mb-2">Processing Intent</label>
					<select
						bind:value={processingIntent}
						class="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-white focus:ring-2 focus:ring-primary focus:border-transparent"
					>
						<option value="code">Code</option>
						<option value="comment">Comments</option>
						<option value="documentation">Documentation</option>
					</select>
				</div>
			</div>
			
			<div class="flex justify-end mt-6">
				<button
					on:click={() => showSettings = false}
					class="bg-primary hover:bg-primary/80 text-white px-4 py-2 rounded-lg transition-all"
				>
					Done
				</button>
			</div>
		</div>
	</div>
{/if}

<!-- Results Modal -->
{#if showResults && $transcriptionResults.length > 0}
	<div class="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
		<div class="bg-gray-800 rounded-2xl p-6 max-w-4xl w-full max-h-[80vh] overflow-hidden border border-gray-700">
			<div class="flex items-center justify-between mb-6">
				<h3 class="text-lg font-semibold text-white">Transcription Results</h3>
				<div class="flex space-x-2">
					<button
						on:click={clearResults}
						class="text-gray-400 hover:text-white p-2 rounded-lg hover:bg-gray-700 transition-all"
						title="Clear all results"
					>
						<i class="fas fa-trash"></i>
					</button>
					<button
						on:click={() => showResults = false}
						class="text-gray-400 hover:text-white p-2 rounded-lg hover:bg-gray-700 transition-all"
					>
						<i class="fas fa-times"></i>
					</button>
				</div>
			</div>
			
			<div class="overflow-y-auto max-h-[60vh] space-y-4">
				{#each $transcriptionResults.reverse() as result, index}
					<div class="bg-gray-900/50 rounded-lg p-4 border border-gray-600">
						<div class="flex items-center justify-between mb-2">
							<div class="text-sm text-gray-400">
								{result.timestamp.toLocaleTimeString()}
								{#if result.processingTime}
									• {Math.round(result.processingTime)}ms
								{/if}
								{#if result.confidence}
									• {Math.round(result.confidence * 100)}% confidence
								{/if}
							</div>
							
							<button
								on:click={() => copyToClipboard(result.text)}
								class="text-gray-400 hover:text-white p-1 rounded transition-all"
								title="Copy to clipboard"
							>
								<i class="fas fa-copy text-xs"></i>
							</button>
						</div>
						
						<pre class="text-gray-200 whitespace-pre-wrap font-mono text-sm mb-2">{result.text}</pre>
						
						{#if result.originalText && result.originalText !== result.text}
							<details class="text-xs">
								<summary class="text-gray-400 cursor-pointer hover:text-gray-300">
									Show original transcription
								</summary>
								<pre class="text-gray-500 whitespace-pre-wrap font-mono mt-2 pl-4">{result.originalText}</pre>
							</details>
						{/if}
					</div>
				{/each}
			</div>
		</div>
	</div>
{/if}