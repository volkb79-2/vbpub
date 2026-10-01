import adapter from '@sveltejs/adapter-node';
import { vitePreprocess } from '@sveltejs/kit/vite';

/** @type {import('@sveltejs/kit').Config} */
const config = {
	// Consult https://kit.svelte.dev/docs/integrations#preprocessors
	preprocess: vitePreprocess(),

	kit: {
		adapter: adapter({
			// Default options for Node.js adapter
			out: 'build',
			precompress: false,
			envPrefix: ''
		}),
		
		// Development configuration
		files: {
			assets: 'static',
			hooks: {
				client: 'src/hooks.client.js',
				server: 'src/hooks.server.js'
			},
			lib: 'src/lib',
			params: 'src/params',
			routes: 'src/routes',
			serviceWorker: 'src/service-worker.js',
			appTemplate: 'src/app.html'
		}
	}
};

export default config;