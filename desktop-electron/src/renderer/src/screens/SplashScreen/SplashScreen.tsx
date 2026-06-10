import { useEffect } from "react";
import splashBg from "../../assets/qiqiclawbg.webp";

interface SplashScreenProps {
  onFinished: () => void;
}

function SplashScreen({ onFinished }: SplashScreenProps): React.JSX.Element {
  useEffect(() => {
    onFinished();
  }, [onFinished]);

  return (
    <div className="splash-screen">
      <img className="splash-bg" src={splashBg} alt="" />
      <div className="splash-logo" aria-label="QiQiClaw Desktop">
        QIQI-CLAW
      </div>
    </div>
  );
}

export default SplashScreen;
