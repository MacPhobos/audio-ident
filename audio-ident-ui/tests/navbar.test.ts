import { describe, it, expect } from 'vitest';

describe('NavBar active state logic', () => {
	function isSearchActive(pathname: string): boolean {
		return pathname === '/search';
	}

	function isTracksActive(pathname: string): boolean {
		return pathname.startsWith('/tracks');
	}

	it('isSearchActive returns true for /search', () => {
		expect(isSearchActive('/search')).toBe(true);
	});

	it('isSearchActive returns false for /', () => {
		expect(isSearchActive('/')).toBe(false);
	});

	it('isSearchActive returns false for /tracks', () => {
		expect(isSearchActive('/tracks')).toBe(false);
	});

	it('isTracksActive returns true for /tracks', () => {
		expect(isTracksActive('/tracks')).toBe(true);
	});

	it('isTracksActive returns true for /tracks/some-id', () => {
		expect(isTracksActive('/tracks/some-id')).toBe(true);
	});

	it('isTracksActive returns false for /search', () => {
		expect(isTracksActive('/search')).toBe(false);
	});

	it('isTracksActive returns false for /', () => {
		expect(isTracksActive('/')).toBe(false);
	});
});
