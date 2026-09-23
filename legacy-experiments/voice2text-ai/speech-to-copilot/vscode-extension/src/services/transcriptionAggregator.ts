/**
 * TranscriptionAggregator
 * Keeps local incremental transcript and performs suffix computation.
 * Mirrors backend logic so future offline / hybrid modes are easier.
 */
export class TranscriptionAggregator {
  private lastTranscript = '';
  private finalizedTranscriptions: string[] = [];

  appendCandidate(full: string): { incremental: string; full: string } {
    let common = 0;
    for (let i = 0; i < Math.min(this.lastTranscript.length, full.length); i++) {
      if (this.lastTranscript[i] === full[i]) common++; else break;
    }
    const incremental = full.slice(common);
    this.lastTranscript = full;
    return { incremental, full };
  }

  finalize(): void {
    if (this.lastTranscript.trim()) {
      this.finalizedTranscriptions.push(this.lastTranscript.trim());
      this.lastTranscript = '';
    }
  }

  getLastTranscription(): string {
    return this.finalizedTranscriptions[this.finalizedTranscriptions.length - 1] || this.lastTranscript;
  }

  getCurrentTranscription(): string {
    return this.lastTranscript;
  }

  getAllTranscriptions(): string[] {
    return [...this.finalizedTranscriptions];
  }

  reset() { 
    this.lastTranscript = ''; 
    this.finalizedTranscriptions = [];
  }
}
