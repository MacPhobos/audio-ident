/**
 * Auto-generated types from OpenAPI schema.
 *
 * Regenerate with: pnpm gen:api
 *
 * This file is a placeholder. Once the backend is running, run the gen:api
 * script to replace this with real types generated from the OpenAPI spec.
 */

export interface paths {
	'/health': {
		get: {
			responses: {
				200: {
					content: {
						'application/json': components['schemas']['HealthResponse'];
					};
				};
			};
		};
	};
	'/api/v1/version': {
		get: {
			responses: {
				200: {
					content: {
						'application/json': components['schemas']['VersionResponse'];
					};
				};
			};
		};
	};
}

export interface components {
	schemas: {
		HealthResponse: {
			status: string;
			version: string;
		};
		VersionResponse: {
			name: string;
			version: string;
			git_sha: string;
			build_time: string;
		};
	};
}
