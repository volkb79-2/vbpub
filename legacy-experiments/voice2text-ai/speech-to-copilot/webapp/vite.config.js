import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [sveltekit()],
	
	// Development server configuration
	server: {
		host: '0.0.0.0',
		port: 3000,
		hmr: {
			port: 3001,
			host: '0.0.0.0'
		},
		watch: {
			usePolling: true,
			interval: 1000
		}
	},
	
	// Build configuration
	build: {
		target: 'es2020',
		sourcemap: true
	},
	
	// Environment variables
	define: {
		__DEV__: process.env.NODE_ENV === 'development'
	},
	
	// Optimizations
	optimizeDeps: {
		include: ['socket.io-client']
	}
});