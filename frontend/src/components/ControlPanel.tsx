import { useState } from 'react';
import { AlertTriangle } from 'lucide-react';

interface ControlPanelProps {
  tradingEnabled: boolean;
  onToggle: () => Promise<void>;
  onPanicSell: () => Promise<void>;
}

const ControlPanel = ({ tradingEnabled, onToggle, onPanicSell }: ControlPanelProps) => {
  const [showPanicModal, setShowPanicModal] = useState(false);
  const [toggleLoading, setToggleLoading] = useState(false);
  const [panicLoading, setPanicLoading] = useState(false);

  const handleToggleBot = async () => {
    setToggleLoading(true);
    try {
      await onToggle();
    } finally {
      setToggleLoading(false);
    }
  };

  const handlePanicSell = async () => {
    setPanicLoading(true);
    try {
      await onPanicSell();
      setShowPanicModal(false);
    } finally {
      setPanicLoading(false);
    }
  };

  return (
    <>
      <div className="bg-gray-800 p-6 rounded-xl shadow-lg flex flex-col space-y-4">
        <h3 className="text-lg font-medium text-gray-200 border-b border-gray-700 pb-3">제어 패널</h3>
        <div className="flex items-center justify-between">
          <span className="font-medium">자동매매 봇</span>
          <button
            onClick={handleToggleBot}
            disabled={toggleLoading}
            className={`relative inline-flex items-center h-6 rounded-full w-11 transition-colors ${
              tradingEnabled ? 'bg-green-500' : 'bg-gray-600'
            } ${toggleLoading ? 'opacity-60 cursor-not-allowed' : ''}`}
          >
            <span
              className={`inline-block w-4 h-4 transform bg-white rounded-full transition-transform ${
                tradingEnabled ? 'translate-x-6' : 'translate-x-1'
              }`}
            />
          </button>
        </div>
        <button
          onClick={() => setShowPanicModal(true)}
          disabled={panicLoading}
          className="w-full bg-red-600 hover:bg-red-700 disabled:opacity-60 text-white font-bold py-3 px-4 rounded-lg flex items-center justify-center transition-colors duration-200"
        >
          <AlertTriangle className="mr-2 h-5 w-5" />
          전량 매도 (Panic)
        </button>
      </div>

      {showPanicModal && (
        <div className="fixed inset-0 bg-black bg-opacity-70 flex justify-center items-center z-50">
          <div className="bg-gray-800 rounded-lg p-8 shadow-2xl max-w-sm w-full">
            <h2 className="text-2xl font-bold text-red-500 flex items-center">
              <AlertTriangle className="mr-3 h-8 w-8" /> 확인 필요
            </h2>
            <p className="mt-4 text-gray-300">
              정말로 모든 보유 종목을 시장가로 매도하시겠습니까? 이 작업은 되돌릴 수 없습니다.
            </p>
            <div className="mt-8 flex justify-end space-x-4">
              <button 
                onClick={() => setShowPanicModal(false)}
                className="px-6 py-2 rounded-lg bg-gray-600 hover:bg-gray-500 font-semibold transition-colors"
              >
                취소
              </button>
              <button
                onClick={handlePanicSell}
                disabled={panicLoading}
                className="px-6 py-2 rounded-lg bg-red-600 hover:bg-red-700 disabled:opacity-60 font-bold text-white transition-colors"
              >
                {panicLoading ? '처리 중…' : '전량 매도'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
};

export default ControlPanel;
