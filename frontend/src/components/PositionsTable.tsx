import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../api/client';
import { symbolNames } from '../constants/stockMap';
import { useToast } from './Toast';

interface PositionDetail {
  symbol: string;
  quantity: number;
  purchasePrice: number;
  currentPrice: number;
  unrealizedPL: number;
  unrealizedPLPct: number;
  stopPrice: number;
}

interface StatusData {
  positionsDetail?: PositionDetail[];
}

const PositionsTable = () => {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [sellingSymbol, setSellingSymbol] = useState<string | null>(null);

  const { data } = useQuery<StatusData>({
    queryKey: ['status'],
    queryFn: async () => {
      const { data: res } = await apiClient.get('/api/status');
      return res;
    },
  });

  const positions = data?.positionsDetail ?? [];

  const formatCurrency = (value: number) => `₩${value.toLocaleString()}`;

  const handleSell = async (p: PositionDetail) => {
    const name = symbolNames[p.symbol] ?? p.symbol;
    if (!window.confirm(`${name}(${p.symbol}) 전량 ${p.quantity}주를 시장가로 매도하시겠습니까?`)) return;
    setSellingSymbol(p.symbol);
    try {
      const { data: res } = await apiClient.post<{ success: boolean; message: string }>('/api/sell', { symbol: p.symbol });
      if (res.success) {
        showToast('success', res.message);
        queryClient.invalidateQueries({ queryKey: ['status'] });
        queryClient.invalidateQueries({ queryKey: ['trades'] });
      } else {
        showToast('error', res.message ?? '매도 요청 실패');
      }
    } catch (e: unknown) {
      const msg = e && typeof e === 'object' && 'response' in e && typeof (e as { response?: { data?: { message?: string } } }).response?.data?.message === 'string'
        ? (e as { response: { data: { message: string } } }).response.data.message
        : '매도 요청 중 오류가 발생했습니다.';
      showToast('error', msg);
    } finally {
      setSellingSymbol(null);
    }
  };

  if (positions.length === 0) {
    return (
      <div className="bg-gray-800 p-6 rounded-xl shadow-lg">
        <h3 className="text-lg font-medium text-gray-200 mb-4">보유 포지션</h3>
        <p className="text-gray-500">보유 중인 종목이 없습니다.</p>
      </div>
    );
  }

  return (
    <div className="bg-gray-800 p-6 rounded-xl shadow-lg overflow-x-auto">
      <h3 className="text-lg font-medium text-gray-200 mb-4">보유 포지션</h3>
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-gray-600 text-gray-400">
            <th className="py-2 pr-4">종목</th>
            <th className="py-2 pr-4">수량</th>
            <th className="py-2 pr-4">매수가</th>
            <th className="py-2 pr-4">현재가</th>
            <th className="py-2 pr-4">평가손익</th>
            <th className="py-2 pr-4">수익률</th>
            <th className="py-2 pr-4">손절가</th>
            <th className="py-2 w-20">매도</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => {
            const isProfit = p.unrealizedPL >= 0;
            const isSelling = sellingSymbol === p.symbol;
            return (
              <tr key={p.symbol} className="border-b border-gray-700">
                <td className="py-2 pr-4 font-medium text-gray-200">{symbolNames[p.symbol] ?? p.symbol}</td>
                <td className="py-2 pr-4 text-gray-300">{p.quantity}</td>
                <td className="py-2 pr-4 text-gray-300">{formatCurrency(p.purchasePrice)}</td>
                <td className="py-2 pr-4 text-gray-300">{formatCurrency(p.currentPrice)}</td>
                <td className={`py-2 pr-4 ${isProfit ? 'text-green-400' : 'text-red-400'}`}>
                  {formatCurrency(p.unrealizedPL)}
                </td>
                <td className={`py-2 pr-4 ${isProfit ? 'text-green-400' : 'text-red-400'}`}>
                  {p.unrealizedPLPct.toFixed(2)}%
                </td>
                <td className="py-2 pr-4 text-gray-400">{formatCurrency(p.stopPrice)}</td>
                <td className="py-2">
                  <button
                    type="button"
                    onClick={() => handleSell(p)}
                    disabled={isSelling}
                    className="px-3 py-1.5 rounded-lg bg-amber-600 hover:bg-amber-500 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium transition-colors"
                  >
                    {isSelling ? '처리 중…' : '매도'}
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
};

export default PositionsTable;
