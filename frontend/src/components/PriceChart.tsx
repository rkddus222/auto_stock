import { useQuery } from '@tanstack/react-query';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend } from 'recharts';
import { apiClient } from '../api/client';

interface HistoryPoint {
  timestamp: string | null;
  totalAssets: number;
  cashBalance: number;
  holdingsValue: number;
  realizedPL: number;
  unrealizedPL: number;
  dailyReturnPct: number;
}

function formatTime(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' });
}

const PriceChart = () => {
  const { data: history = [] } = useQuery<HistoryPoint[]>({
    queryKey: ['portfolio-history'],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/portfolio/history?days=7');
      return data;
    },
    refetchInterval: 60_000,
  });

  const chartData = history.map((p) => ({
    time: formatTime(p.timestamp),
    totalAssets: p.totalAssets,
    cashBalance: p.cashBalance,
    holdingsValue: p.holdingsValue,
  }));

  return (
    <div className="bg-gray-800 p-6 rounded-xl shadow-lg h-full">
      <h3 className="text-lg font-medium text-gray-200 mb-4">포트폴리오 추이 (총자산 / 현금 / 보유주식)</h3>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={chartData}>
          <CartesianGrid strokeDasharray="3 3" stroke="#4a5568" />
          <XAxis dataKey="time" stroke="#a0aec0" />
          <YAxis stroke="#a0aec0" domain={['auto', 'auto']} tickFormatter={(v) => `${(v / 10000).toFixed(0)}만`} />
          <Tooltip
            contentStyle={{ backgroundColor: '#2d3748', border: 'none' }}
            labelStyle={{ color: '#e2e8f0' }}
            formatter={(value: number, name: string) => [`₩${value.toLocaleString()}`, name]}
            labelFormatter={(label) => label}
          />
          <Legend />
          <Line type="monotone" dataKey="totalAssets" name="총자산" stroke="#48bb78" strokeWidth={2} dot={false} />
          <Line type="monotone" dataKey="cashBalance" name="현금" stroke="#63b3ed" strokeWidth={2} dot={false} />
          <Line type="monotone" dataKey="holdingsValue" name="보유주식" stroke="#f6ad55" strokeWidth={2} dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
};

export default PriceChart;
