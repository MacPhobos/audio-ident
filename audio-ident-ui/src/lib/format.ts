/**
 * Formatting utilities for track display.
 */

/** Format seconds as `m:ss`. */
export function formatDuration(seconds: number): string {
	const m = Math.floor(seconds / 60);
	const s = Math.floor(seconds % 60);
	return `${m}:${s.toString().padStart(2, '0')}`;
}

/** Format an ISO 8601 date-time string for display (e.g. "Feb 14, 2026 10:30 AM"). */
export function formatDate(iso: string): string {
	const date = new Date(iso);
	return date.toLocaleDateString('en-US', {
		year: 'numeric',
		month: 'short',
		day: 'numeric',
		hour: 'numeric',
		minute: '2-digit'
	});
}

/** Format bytes to a human-readable file size (KB / MB / GB). */
export function formatFileSize(bytes: number): string {
	if (bytes < 1024) return `${bytes} B`;
	if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
	if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
	return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

/** Format bitrate in kbps. */
export function formatBitrate(bps: number | null | undefined): string {
	if (bps == null) return 'Unknown';
	// Backend may return bps or kbps -- if value > 10000, assume bps
	const kbps = bps > 10_000 ? Math.round(bps / 1000) : Math.round(bps);
	return `${kbps} kbps`;
}

/** Format sample rate in Hz. */
export function formatSampleRate(hz: number | null | undefined): string {
	if (hz == null) return 'Unknown';
	return `${hz} Hz`;
}

/** Format channel count as human-readable. */
export function formatChannels(channels: number | null | undefined): string {
	if (channels == null) return 'Unknown';
	if (channels === 1) return '1 (mono)';
	if (channels === 2) return '2 (stereo)';
	return String(channels);
}
