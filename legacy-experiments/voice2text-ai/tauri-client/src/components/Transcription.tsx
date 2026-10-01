import React from 'react';
import { writeText } from '@tauri-apps/api/clipboard';

interface TranscriptionProps {
  text: string;
}

const Transcription: React.FC<TranscriptionProps> = ({ text }) => {
  const copyToClipboard = async () => {
    try {
      await writeText(text);
      alert('Copied to clipboard!');
    } catch (err) {
      alert(`Failed to copy: ${err}`);
    }
  };

  if (!text) {
    return null;
  }

  return (
    <div className="transcription">
      <h2>Transcription</h2>
      <div className="transcription-text">{text}</div>
      <button onClick={copyToClipboard} style={{ marginTop: '1rem' }}>
        📋 Copy to Clipboard
      </button>
    </div>
  );
};

export default Transcription;
