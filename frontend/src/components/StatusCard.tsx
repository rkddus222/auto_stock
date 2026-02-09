import { FC } from 'react';

interface StatusCardProps {
  title: string;
  value: string;
  change?: string;
  isProfit?: boolean;
  subtitle?: string;
}

const StatusCard: FC<StatusCardProps> = ({ title, value, change, isProfit, subtitle }) => {
  const changeColor = isProfit === undefined ? 'text-gray-300' : isProfit ? 'text-red-500' : 'text-blue-500';

  return (
    <div className="bg-gray-800 p-6 rounded-xl shadow-lg">
      <h3 className="text-lg font-medium text-gray-400">{title}</h3>
      <p className="text-3xl font-semibold text-white mt-2">{value}</p>
      {subtitle && (
        <p className="text-sm text-amber-400 mt-1 break-words">{subtitle}</p>
      )}
      {change && (
        <p className={`text-lg font-medium mt-1 ${changeColor}`}>
          {change}
        </p>
      )}
    </div>
  );
};

export default StatusCard;
