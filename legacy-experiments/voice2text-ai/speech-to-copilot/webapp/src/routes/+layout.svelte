<!-- Main Layout -->
<script>
	import { onMount } from 'svelte';
	import { wsClient, isConnected, connectionError } from '$lib/websocket-client.js';
	
	let connectionStatus = 'disconnected';
	
	// Subscribe to connection state
	$: connectionStatus = $isConnected ? 'connected' : 'disconnected';
	
	onMount(async () => {
		// Initialize WebSocket connection on app start
		try {
			await wsClient.connect();
		} catch (error) {
			console.error('Failed to establish initial connection:', error);
		}
		
		// Cleanup on unmount
		return () => {
			wsClient.disconnect();
		};
	});
</script>

<div class="min-h-screen bg-gradient-to-br from-gray-900 via-gray-800 to-gray-900">
	<!-- Header -->
	<header class="bg-gray-800/50 backdrop-blur-sm border-b border-gray-700">
		<div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4">
			<div class="flex items-center justify-between">
				<div class="flex items-center space-x-3">
					<div class="w-8 h-8 bg-gradient-to-r from-primary to-secondary rounded-lg flex items-center justify-center">
						<i class="fas fa-microphone text-white text-sm"></i>
					</div>
					<div>
						<h1 class="text-xl font-bold text-white">Speech-to-Copilot</h1>
						<p class="text-sm text-gray-400">Enhanced voice coding assistant</p>
					</div>
				</div>
				
				<!-- Connection Status -->
				<div class="flex items-center space-x-2">
					<div class="flex items-center space-x-2">
						<div class="w-2 h-2 rounded-full {connectionStatus === 'connected' ? 'bg-green-500' : 'bg-red-500'}"></div>
						<span class="text-sm text-gray-300 capitalize">{connectionStatus}</span>
					</div>
					
					{#if $connectionError}
						<div class="text-xs text-red-400 max-w-xs truncate" title={$connectionError}>
							{$connectionError}
						</div>
					{/if}
				</div>
			</div>
		</div>
	</header>
	
	<!-- Main Content -->
	<main class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
		<slot />
	</main>
	
	<!-- Footer -->
	<footer class="bg-gray-800/30 border-t border-gray-700 mt-auto">
		<div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
			<div class="flex items-center justify-between text-sm text-gray-400">
				<div class="flex items-center space-x-4">
					<span>Speech-to-Copilot v0.1.0</span>
					<span>•</span>
					<span>Development Environment</span>
				</div>
				
				<div class="flex items-center space-x-4">
					<a href="/debug" class="hover:text-white transition-colors">
						<i class="fas fa-bug mr-1"></i>
						Debug
					</a>
					<a href="http://localhost:8000/docs" target="_blank" class="hover:text-white transition-colors">
						<i class="fas fa-book mr-1"></i>
						API Docs
					</a>
					<a href="http://localhost:8080" target="_blank" class="hover:text-white transition-colors">
						<i class="fas fa-tachometer-alt mr-1"></i>
						Dashboard
					</a>
				</div>
			</div>
		</div>
	</footer>
</div>

<style>
	:global(body) {
		font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
	}
	
	:global(.primary) {
		color: #667eea;
	}
	
	:global(.secondary) {
		color: #764ba2;
	}
</style>