import { useQuery } from '@tanstack/react-query';
import { apiClient } from '../api/client';
import { symbolNames } from '../constants/stockMap';

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
  const { data } = useQuery<StatusData>({
    queryKey: ['status'],
    queryFn: async () => {
      const { data: res } = await apiClient.get('/api/status');
      return res;
    },
  });

  const positions = data?.positionsDetail ?? [];

  const formatCurrency = (value: number) => `₩${value.toLocaleString()}`;

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
            <th className="py-2">손절가</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => {
            const isProfit = p.unrealizedPL >= 0;
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
                <td className="py-2 text-gray-400">{formatCurrency(p.stopPrice)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
};

export default PositionsTable;
