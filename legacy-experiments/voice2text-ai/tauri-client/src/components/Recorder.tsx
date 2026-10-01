import React from 'react';

interface RecorderProps {
  isRecording: boolean;
  onStart: () => void;
  onStop: () => void;
  disabled?: boolean;
}

const Recorder: React.FC<RecorderProps> = ({ isRecording, onStart, onStop, disabled }) => {
  return (
    <div className={`recorder ${isRecording ? 'recording' : ''}`}>
      <h2>{isRecording ? 'Recording...' : 'Ready to Record'}</h2>
      <button 
        onClick={isRecording ? onStop : onStart}
        disabled={disabled}
      >
        {isRecording ? '⏹ Stop Recording' : '🎤 Start Recording'}
      </button>
      {isRecording && (
        <div style={{ marginTop: '1rem' }}>
          <div className="recording-indicator">
            <span style={{ fontSize: '2rem', color: '#d32f2f' }}>●</span>
            <p>Speak now...</p>
          </div>
        </div>
      )}
    </div>
  );
};

export default Recorder;
