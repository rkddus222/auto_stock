import { useState } from 'react';
import { ShoppingCart } from 'lucide-react';
import { apiClient } from '../api/client';
import { symbolNames } from '../constants/stockMap';
import { useToast } from './Toast';

type InputMode = 'quantity' | 'amount';

interface BuyResponse {
  success: boolean;
  message: string;
  symbol?: string;
  quantity?: number;
  price?: number;
  stop_price?: number;
}

const ManualBuy = () => {
  const { showToast } = useToast();
  const [symbol, setSymbol] = useState('');
  const [inputMode, setInputMode] = useState<InputMode>('quantity');
  const [quantity, setQuantity] = useState('');
  const [amount, setAmount] = useState('');  // 만원 단위
  const [loading, setLoading] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);

  const symbolName = symbolNames[symbol] ?? '';
  const displayLabel = symbolName ? `${symbol} (${symbolName})` : symbol;

  const isValid =
    /^\d{6}$/.test(symbol) &&
    ((inputMode === 'quantity' && Number(quantity) > 0) ||
      (inputMode === 'amount' && Number(amount) > 0));

  const handleSubmit = () => {
    if (!isValid) return;
    setShowConfirm(true);
  };

  const handleConfirmBuy = async () => {
    setLoading(true);
    try {
      const payload: Record<string, string | number> = { symbol };
      if (inputMode === 'quantity') {
        payload.quantity = Number(quantity);
      } else {
        payload.amount = Number(amount) * 10000; // 만원 → 원
      }
      const { data } = await apiClient.post<BuyResponse>('/api/buy', payload);
      if (data.success) {
        showToast('success', data.message);
        setSymbol('');
        setQuantity('');
        setAmount('');
      } else {
        showToast('error', data.message);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '매수 요청 실패';
      showToast('error', msg);
    } finally {
      setLoading(false);
      setShowConfirm(false);
    }
  };

  return (
    <>
      <div className="bg-gray-800 p-6 rounded-xl shadow-lg flex flex-col space-y-4">
        <h3 className="text-lg font-medium text-gray-200 border-b border-gray-700 pb-3 flex items-center">
          <ShoppingCart className="mr-2 h-5 w-5" />
          수동 매수
        </h3>

        {/* 종목코드 */}
        <div>
          <label className="block text-sm text-gray-400 mb-1">종목코드</label>
          <input
            type="text"
            maxLength={6}
            placeholder="005930"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.replace(/\D/g, '').slice(0, 6))}
            className="w-full bg-gray-700 text-white rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          {symbolName && (
            <p className="text-xs text-blue-400 mt-1">{symbolName}</p>
          )}
        </div>

        {/* 수량/금액 토글 */}
        <div className="flex gap-2">
          <button
            onClick={() => setInputMode('quantity')}
            className={`flex-1 py-1.5 rounded-lg text-sm font-medium transition-colors ${
              inputMode === 'quantity'
                ? 'bg-blue-600 text-white'
                : 'bg-gray-700 text-gray-400 hover:bg-gray-600'
            }`}
          >
            수량
          </button>
          <button
            onClick={() => setInputMode('amount')}
            className={`flex-1 py-1.5 rounded-lg text-sm font-medium transition-colors ${
              inputMode === 'amount'
                ? 'bg-blue-600 text-white'
                : 'bg-gray-700 text-gray-400 hover:bg-gray-600'
            }`}
          >
            금액
          </button>
        </div>

        {/* 수량 또는 금액 입력 */}
        {inputMode === 'quantity' ? (
          <div>
            <label className="block text-sm text-gray-400 mb-1">수량 (주)</label>
            <input
              type="number"
              min={1}
              placeholder="1"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              className="w-full bg-gray-700 text-white rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
        ) : (
          <div>
            <label className="block text-sm text-gray-400 mb-1">금액 (만원)</label>
            <input
              type="number"
              min={1}
              placeholder="100"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              className="w-full bg-gray-700 text-white rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            {Number(amount) > 0 && (
              <p className="text-xs text-gray-500 mt-1">
                = {(Number(amount) * 10000).toLocaleString()}원
              </p>
            )}
          </div>
        )}

        {/* 매수 버튼 */}
        <button
          onClick={handleSubmit}
          disabled={!isValid || loading}
          className="w-full bg-blue-600 hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed text-white font-bold py-3 px-4 rounded-lg flex items-center justify-center transition-colors duration-200"
        >
          <ShoppingCart className="mr-2 h-5 w-5" />
          시장가 매수
        </button>
      </div>

      {/* 확인 모달 */}
      {showConfirm && (
        <div className="fixed inset-0 bg-black bg-opacity-70 flex justify-center items-center z-50">
          <div className="bg-gray-800 rounded-lg p-8 shadow-2xl max-w-sm w-full">
            <h2 className="text-xl font-bold text-blue-400 flex items-center">
              <ShoppingCart className="mr-3 h-6 w-6" /> 매수 확인
            </h2>
            <div className="mt-4 space-y-2 text-gray-300">
              <p>
                <span className="text-gray-500">종목:</span>{' '}
                <span className="font-semibold text-white">{displayLabel}</span>
              </p>
              {inputMode === 'quantity' ? (
                <p>
                  <span className="text-gray-500">수량:</span>{' '}
                  <span className="font-semibold text-white">{Number(quantity).toLocaleString()}주</span>
                </p>
              ) : (
                <p>
                  <span className="text-gray-500">금액:</span>{' '}
                  <span className="font-semibold text-white">{Number(amount).toLocaleString()}만원</span>
                </p>
              )}
              <p className="text-sm text-yellow-400 mt-2">시장가로 즉시 매수됩니다.</p>
            </div>
            <div className="mt-8 flex justify-end space-x-4">
              <button
                onClick={() => setShowConfirm(false)}
                className="px-6 py-2 rounded-lg bg-gray-600 hover:bg-gray-500 font-semibold transition-colors"
              >
                취소
              </button>
              <button
                onClick={handleConfirmBuy}
                disabled={loading}
                className="px-6 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 disabled:opacity-60 font-bold text-white transition-colors"
              >
                {loading ? '처리 중…' : '매수'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
};

export default ManualBuy;
