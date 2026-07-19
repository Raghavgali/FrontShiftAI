import React from 'react';

const FrontShiftLogo = ({ size = 40, showText = true, className = "" }) => {
    const iconSize = size * 0.6;
    const textSize = size * 0.5;

    return (
        <div className={`flex items-center gap-3 ${className}`}>
            {/* Icon Container */}
            <div
                className="rounded-xl bg-gradient-to-br from-white via-gray-200 to-gray-400 flex items-center justify-center shadow-[0_0_15px_rgba(255,255,255,0.15)] border border-white/50"
                style={{ width: size, height: size }}
            >
                <svg
                    width={iconSize}
                    height={iconSize}
                    viewBox="0 0 24 24"
                    fill="none"
                >
                    <path
                        d="M7 6h10M7 6v12M7 13h7"
                        stroke="#1e293b"
                        strokeWidth="2.5"
                        strokeLinecap="round"
                    />
                    <path
                        d="M14 13l3 2-2.5 2.5"
                        stroke="#1e293b"
                        strokeWidth="2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                    />
                </svg>
            </div>

            {/* Text */}
            {showText && (
                <span className="text-white font-bold" style={{ fontSize: `${textSize}px` }}>
                    FrontShiftAI
                </span>
            )}
        </div>
    );
};

export default FrontShiftLogo;
