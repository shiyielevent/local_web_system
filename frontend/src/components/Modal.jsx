import React from 'react';

export default function Modal({ title, open, onClose, children, width = 600 }) {
  if (!open) return null;
  return (
    <div className="modal-mask" onClick={onClose}>
      <div
        className="modal-panel"
        style={{ width }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <h3>{title}</h3>
          <button className="ghost-btn" onClick={onClose}>关闭</button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}
