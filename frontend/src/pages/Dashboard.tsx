import { useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../api/client';
import StatusCard from '../components/StatusCard';
import ControlPanel from '../components/ControlPanel';
import TradeHistory from '../components/TradeHistory';
import PriceChart from '../components/PriceChart';
import PositionsTable from '../components/PositionsTable';
import StrategySettings from '../components/StrategySettings';
import { useToast } from '../components/Toast';
import { useWebSocket } from '../hooks/useWebSocket';

interface StatusData {
  totalAssets: number;
  cashBalance: number;
  holdingsValue: number;
  todayRealizedPL: number;
  returnRate: number;
  tradingEnabled: boolean;
  positions: Record<string, { bought: boolean; purchase_price: number; quantity: number; stop_price: number }>;
  positionsDetail?: Array<{ symbol: string; quantity: number; purchasePrice: number; currentPrice: number; unrealizedPL: number; unrealizedPLPct: number; stopPrice: number }>;
  targetSymbols: string[];
  assetsError?: string | null;
}

const Dashboard = () => {
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const { isConnected } = useWebSocket({
    onStatusUpdate: (payload) => {
      queryClient.setQueryData(['status'], payload);
    },
    onTradeEvent: (msg) => {
      queryClient.invalidateQueries({ queryKey: ['status'] });
      queryClient.invalidateQueries({ queryKey: ['trades'] });
      showToast('info', `${msg.symbol} ${msg.side} ${msg.quantity}주 @ ${msg.price?.toLocaleString() ?? '-'}`);
    },
  });

  const { data: statusData, error, isLoading } = useQuery<StatusData>({
    queryKey: ['status'],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/status');
      return data;
    },
    refetchInterval: isConnected ? false : 5000,
  });

  const formatCurrency = (value: number) => new Intl.NumberFormat('ko-KR').format(value);

  const displayData = statusData ?? {
    totalAssets: 0,
    cashBalance: 0,
    holdingsValue: 0,
    todayRealizedPL: 0,
    returnRate: 0,
    tradingEnabled: false,
    assetsError: null,
  };
  const isProfit = displayData.todayRealizedPL >= 0;
  const hasAssetsError = !!displayData.assetsError;

  if (error) {
    return (
      <div className="min-h-screen bg-gray-900 text-white p-8 flex items-center justify-center">
        <p className="text-red-400">서버 상태를 불러올 수 없습니다. 백엔드가 실행 중인지 확인하세요.</p>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-900 text-white p-4 sm:p-6 lg:p-8">
      <div className="max-w-7xl mx-auto">
        <header className="mb-8 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-4xl font-bold tracking-tight">투자 대시보드</h1>
            <p className="text-gray-400 mt-1">실시간 자산 및 봇 상태를 확인하세요.</p>
          </div>
          <span
            className={`shrink-0 px-3 py-1 rounded-full text-sm font-medium ${
              isConnected ? 'bg-green-900/60 text-green-300' : 'bg-gray-700 text-gray-400'
            }`}
          >
            {isConnected ? '실시간 연결됨' : '연결 끊김 (폴링)'}
          </span>
        </header>

        {/* Main Grid - items-start로 각 컬럼 높이 독립 */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 items-start">
          <div className="lg:col-span-2 space-y-6">
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              <StatusCard
                title="현금 (예수금)"
                value={isLoading ? '…' : hasAssetsError ? '조회 실패' : `₩${formatCurrency(displayData.cashBalance ?? 0)}`}
                subtitle={hasAssetsError ? displayData.assetsError : undefined}
              />
              <StatusCard
                title="보유 주식"
                value={isLoading ? '…' : hasAssetsError ? '-' : `₩${formatCurrency(displayData.holdingsValue ?? 0)}`}
              />
              <StatusCard
                title="총 자산"
                value={isLoading ? '…' : hasAssetsError ? '-' : `₩${formatCurrency((displayData.cashBalance ?? 0) + (displayData.holdingsValue ?? 0))}`}
                subtitle="현금 + 보유 주식 (매수 시 현금 ↓·보유 ↑, 합계 동일)"
              />
              <StatusCard
                title="당일 실현 손익"
                value={isLoading ? '…' : `₩${formatCurrency(displayData.todayRealizedPL)}`}
                change={isLoading ? undefined : `${displayData.returnRate.toFixed(2)}%`}
                isProfit={isProfit}
              />
            </div>
            <PriceChart />
            <PositionsTable />
          </div>

          <div className="lg:col-span-1 space-y-6 flex flex-col">
            <ControlPanel
              tradingEnabled={displayData.tradingEnabled}
              onToggle={async () => {
                const endpoint = displayData.tradingEnabled ? '/api/bot/stop' : '/api/bot/start';
                await apiClient.post(endpoint);
                await queryClient.invalidateQueries({ queryKey: ['status'] });
              }}
              onPanicSell={async () => {
                await apiClient.post('/api/panic-sell');
                await queryClient.invalidateQueries({ queryKey: ['status'] });
                await queryClient.invalidateQueries({ queryKey: ['trades'] });
              }}
            />
            <StrategySettings />
            <TradeHistory />
          </div>
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
