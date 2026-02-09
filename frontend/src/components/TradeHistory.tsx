import { useQuery } from '@tanstack/react-query';
import { apiClient } from '../api/client';

interface TradeItem {
  id: number;
  timestamp: string | null;
  symbol: string;
  orderType: string;
  price: number;
  quantity: number;
  status: string;
}

const symbolNames: Record<string, string> = {
  '005930': '삼성전자',
  '000660': 'SK하이닉스',
};

const TradeHistory = () => {
  const { data: trades = [], isLoading } = useQuery<TradeItem[]>({
    queryKey: ['trades'],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/trades');
      return data;
    },
    refetchInterval: 10000,
  });

  const formatTime = (iso: string | null) => {
    if (!iso) return '-';
    const d = new Date(iso);
    return d.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  };

  return (
    <div className="bg-gray-800 p-6 rounded-xl shadow-lg h-full">
      <h3 className="text-lg font-medium text-gray-200 border-b border-gray-700 pb-3 mb-4">매매 로그</h3>
      <div className="space-y-3 overflow-y-auto h-[calc(100%-40px)]">
        {isLoading ? (
          <p className="text-gray-400 text-sm">불러오는 중…</p>
        ) : trades.length === 0 ? (
          <p className="text-gray-400 text-sm">최근 매매 내역이 없습니다.</p>
        ) : (
          trades.map((trade) => (
            <div key={trade.id} className="grid grid-cols-4 gap-2 text-sm">
              <span className={trade.orderType === 'BUY' ? 'text-red-500' : 'text-blue-500'}>
                {trade.orderType === 'BUY' ? '매수' : '매도'}
              </span>
              <span className="font-medium truncate col-span-2">
                {symbolNames[trade.symbol] ?? trade.symbol} ({trade.quantity}주)
              </span>
              <span className="text-right text-gray-400">{formatTime(trade.timestamp)}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
};

export default TradeHistory;
