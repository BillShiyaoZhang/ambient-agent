(function() {
  const root = document.getElementById('weather-app-full') || document.currentScript.parentElement;
  
  // WMO Weather codes mapping
  const weatherCodes = {
    0: { desc: '晴朗', icon: '☀️', day: 'sunny' },
    1: { desc: '大致晴朗', icon: '🌤️', day: 'sunny' },
    2: { desc: '局部多云', icon: '⛅', day: 'cloudy' },
    3: { desc: '阴天', icon: '☁️', day: 'cloudy' },
    45: { desc: '雾', icon: '🌫️', day: 'cloudy' },
    48: { desc: '冻雾', icon: '🌫️', day: 'cloudy' },
    51: { desc: '小毛毛雨', icon: '🌦️', day: 'rainy' },
    53: { desc: '毛毛雨', icon: '🌦️', day: 'rainy' },
    55: { desc: '大毛毛雨', icon: '🌧️', day: 'rainy' },
    56: { desc: '冻毛毛雨', icon: '🌧️', day: 'rainy' },
    57: { desc: '强冻毛毛雨', icon: '🌧️', day: 'rainy' },
    61: { desc: '小雨', icon: '🌧️', day: 'rainy' },
    63: { desc: '中雨', icon: '🌧️', day: 'rainy' },
    65: { desc: '大雨', icon: '🌧️', day: 'rainy' },
    66: { desc: '冻雨', icon: '🌧️', day: 'rainy' },
    67: { desc: '强冻雨', icon: '🌧️', day: 'rainy' },
    71: { desc: '小雪', icon: '🌨️', day: 'snowy' },
    73: { desc: '中雪', icon: '❄️', day: 'snowy' },
    75: { desc: '大雪', icon: '❄️', day: 'snowy' },
    77: { desc: '雪粒', icon: '❄️', day: 'snowy' },
    80: { desc: '小阵雨', icon: '🌦️', day: 'rainy' },
    81: { desc: '阵雨', icon: '🌧️', day: 'rainy' },
    82: { desc: '强阵雨', icon: '⛈️', day: 'rainy' },
    85: { desc: '小阵雪', icon: '🌨️', day: 'snowy' },
    86: { desc: '强阵雪', icon: '🌨️', day: 'snowy' },
    95: { desc: '雷暴', icon: '⛈️', day: 'thunder' },
    96: { desc: '雷暴伴小冰雹', icon: '⛈️', day: 'thunder' },
    99: { desc: '雷暴伴大冰雹', icon: '⛈️', day: 'thunder' }
  };

  const windDirs = ['北', '东北', '东', '东南', '南', '西南', '西', '西北'];
  
  function getWindDir(deg) {
    return windDirs[Math.round(deg / 45) % 8];
  }
  
  function getUVLevel(uv) {
    if (uv < 3) return ['低', '#4caf50'];
    if (uv < 6) return ['中等', '#ffeb3b'];
    if (uv < 8) return ['高', '#ff9800'];
    if (uv < 11) return ['很高', '#f44336'];
    return ['极高', '#9c27b0'];
  }
  
  function getAQILevel(aqi) {
    if (aqi <= 50) return { level: '优', color: '#4caf50', desc: '空气质量令人满意,空气污染极少或没有。' };
    if (aqi <= 100) return { level: '良', color: '#8bc34a', desc: '空气质量可接受,某些污染物可能对极少数敏感人群有较弱影响。' };
    if (aqi <= 150) return { level: '轻度污染', color: '#ffc107', desc: '敏感人群可能出现症状,一般人群不会受影响。' };
    if (aqi <= 200) return { level: '中度污染', color: '#ff9800', desc: '一般人群可能开始出现健康影响,敏感人群可能出现更严重的影响。' };
    if (aqi <= 300) return { level: '重度污染', color: '#f44336', desc: '一般人群可能受到健康影响。' };
    return { level: '严重污染', color: '#9c27b0', desc: '健康警告,所有人可能受到严重影响。' };
  }
  
  function setBackground(condition, isDay) {
    const app = root.querySelector('.weather-app');
    app.className = 'weather-app ' + (isDay ? condition : 'night');
    
    const rainContainer = root.querySelector('#rainContainer');
    const snowContainer = root.querySelector('#snowContainer');
    const sun = root.querySelector('#sunOrMoon');
    rainContainer.innerHTML = '';
    snowContainer.innerHTML = '';
    
    if (!isDay) {
      sun.style.background = 'radial-gradient(circle, #e0e0e0, #b0b0b0)';
      sun.style.boxShadow = '0 0 60px rgba(255,255,255,0.4)';
    } else {
      sun.style.background = 'radial-gradient(circle, #ffd700, #ffaa00)';
      sun.style.boxShadow = '0 0 80px rgba(255, 215, 0, 0.6)';
    }
    
    if (condition === 'rainy' || condition === 'thunder') {
      for (let i = 0; i < 60; i++) {
        const drop = document.createElement('div');
        drop.className = 'raindrop';
        drop.style.left = Math.random() * 100 + '%';
        drop.style.animationDuration = (0.5 + Math.random() * 0.5) + 's';
        drop.style.animationDelay = Math.random() * 2 + 's';
        drop.style.opacity = 0.4 + Math.random() * 0.6;
        rainContainer.appendChild(drop);
      }
    } else if (condition === 'snowy') {
      for (let i = 0; i < 40; i++) {
        const flake = document.createElement('div');
        flake.className = 'snowflake';
        flake.textContent = '❄';
        flake.style.left = Math.random() * 100 + '%';
        flake.style.animationDuration = (3 + Math.random() * 4) + 's';
        flake.style.animationDelay = Math.random() * 5 + 's';
        flake.style.fontSize = (10 + Math.random() * 12) + 'px';
        flake.style.opacity = 0.5 + Math.random() * 0.5;
        snowContainer.appendChild(flake);
      }
    }
  }
  
  function formatTime(iso) {
    return iso.split('T')[1].substring(0, 5);
  }
  
  function formatDate(iso, lang = 'zh') {
    const d = new Date(iso);
    const weekDays = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
    const today = new Date();
    const tomorrow = new Date(today);
    tomorrow.setDate(today.getDate() + 1);
    if (d.toDateString() === today.toDateString()) return '今天';
    if (d.toDateString() === tomorrow.toDateString()) return '明天';
    return weekDays[d.getDay()];
  }
  
  function formatCurrentDate() {
    const d = new Date();
    return `${d.getFullYear()}年${d.getMonth()+1}月${d.getDate()}日 · ${['周日','周一','周二','周三','周四','周五','周六'][d.getDay()]}`;
  }
  
  async function geocode(city) {
    const url = `https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(city)}&count=1&language=zh&format=json`;
    const res = await fetch(url);
    const data = await res.json();
    if (!data.results || data.results.length === 0) {
      throw new Error('未找到城市: ' + city);
    }
    return data.results[0];
  }
  
  async function fetchWeather(lat, lon) {
    const url = `https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&current=temperature_2m,relative_humidity_2m,apparent_temperature,is_day,precipitation,rain,showers,snowfall,weather_code,cloud_cover,pressure_msl,surface_pressure,wind_speed_10m,wind_direction_10m,wind_gusts_10m&hourly=temperature_2m,relative_humidity_2m,dew_point_2m,apparent_temperature,precipitation_probability,precipitation,weather_code,cloud_cover,visibility,wind_speed_10m,wind_direction_10m,is_day,uv_index&daily=weather_code,temperature_2m_max,temperature_2m_min,apparent_temperature_max,apparent_temperature_min,sunrise,sunset,daylight_duration,uv_index_max,precipitation_sum,rain_sum,showers_sum,snowfall_sum,precipitation_hours,precipitation_probability_max,wind_speed_10m_max,wind_gusts_10m_max,wind_direction_10m_dominant&timezone=auto&forecast_days=7`;
    const res = await fetch(url);
    return await res.json();
  }
  
  async function fetchAirQuality(lat, lon) {
    const url = `https://air-quality-api.open-meteo.com/v1/air-quality?latitude=${lat}&longitude=${lon}&current=pm10,pm2_5,carbon_monoxide,nitrogen_dioxide,sulphur_dioxide,ozone,us_aqi&timezone=auto`;
    const res = await fetch(url);
    return await res.json();
  }
  
  function renderWeather(loc, weather, air) {
    const current = weather.current;
    const hourly = weather.hourly;
    const daily = weather.daily;
    
    const code = current.weather_code;
    const info = weatherCodes[code] || { desc: '未知', icon: '🌡️', day: 'cloudy' };
    
    setBackground(info.day, current.is_day === 1);
    
    // Current weather
    root.querySelector('#cityName').textContent = loc.name;
    root.querySelector('#countryName').textContent = loc.admin1 ? `${loc.admin1} · ${loc.country || ''}` : (loc.country || '');
    root.querySelector('#currentDate').textContent = formatCurrentDate();
    root.querySelector('#currentIcon').textContent = info.icon;
    root.querySelector('#currentTemp').textContent = Math.round(current.temperature_2m);
    root.querySelector('#currentDesc').textContent = info.desc;
    root.querySelector('#tempHigh').textContent = Math.round(daily.temperature_2m_max[0]);
    root.querySelector('#tempLow').textContent = Math.round(daily.temperature_2m_min[0]);
    
    // Details
    root.querySelector('#feelsLike').textContent = Math.round(current.apparent_temperature) + '°';
    root.querySelector('#humidity').textContent = current.relative_humidity_2m + '%';
    root.querySelector('#windSpeed').textContent = current.wind_speed_10m.toFixed(1) + ' km/h';
    root.querySelector('#windDir').textContent = getWindDir(current.wind_direction_10m);
    root.querySelector('#pressure').textContent = Math.round(current.pressure_msl) + ' hPa';
    root.querySelector('#visibility').textContent = (hourly.visibility[0] / 1000).toFixed(1) + ' km';
    root.querySelector('#cloudCover').textContent = current.cloud_cover + '%';
    
    const uv = hourly.uv_index[0] || 0;
    const [uvLvl, uvColor] = getUVLevel(uv);
    root.querySelector('#uvIndex').textContent = uv.toFixed(1);
    root.querySelector('#uvLevel').textContent = uvLvl;
    
    root.querySelector('#dewPoint').textContent = Math.round(hourly.dew_point_2m[0]) + '°';
    root.querySelector('#sunrise').textContent = formatTime(daily.sunrise[0]);
    root.querySelector('#sunset').textContent = formatTime(daily.sunset[0]);
    root.querySelector('#precipProb').textContent = (hourly.precipitation_probability[0] || 0) + '%';
    
    // Hourly forecast - next 24 hours
    const hourlyScroll = root.querySelector('#hourlyScroll');
    hourlyScroll.innerHTML = '';
    const now = new Date();
    const currentHour = now.getHours();
    let startIdx = hourly.time.findIndex(t => {
      const d = new Date(t);
      return d.getHours() === currentHour && d.toDateString() === now.toDateString();
    });
    if (startIdx < 0) startIdx = 0;
    
    for (let i = startIdx; i < startIdx + 24 && i < hourly.time.length; i++) {
      const t = new Date(hourly.time[i]);
      const isNow = i === startIdx;
      const item = document.createElement('div');
      item.className = 'hourly-item' + (isNow ? ' now' : '');
      const wInfo = weatherCodes[hourly.weather_code[i]] || { icon: '🌡️' };
      item.innerHTML = `
        <div class="hourly-time">${isNow ? '现在' : t.getHours() + ':00'}</div>
        <div class="hourly-icon">${wInfo.icon}</div>
        <div class="hourly-temp">${Math.round(hourly.temperature_2m[i])}°</div>
        ${hourly.precipitation_probability[i] > 10 ? `<div class="hourly-precip">💧${hourly.precipitation_probability[i]}%</div>` : ''}
      `;
      hourlyScroll.appendChild(item);
    }
    
    // Daily forecast
    const dailyList = root.querySelector('#dailyList');
    dailyList.innerHTML = '';
    
    // Find global max/min for bar
    let globalMax = -Infinity, globalMin = Infinity;
    daily.temperature_2m_max.forEach((t, i) => {
      if (t > globalMax) globalMax = t;
      if (daily.temperature_2m_min[i] < globalMin) globalMin = daily.temperature_2m_min[i];
    });
    const range = globalMax - globalMin || 1;
    
    for (let i = 0; i < daily.time.length; i++) {
      const wInfo = weatherCodes[daily.weather_code[i]] || { icon: '🌡️' };
      const low = daily.temperature_2m_min[i];
      const high = daily.temperature_2m_max[i];
      const leftPct = ((low - globalMin) / range) * 100;
      const widthPct = ((high - low) / range) * 100;
      
      const item = document.createElement('div');
      item.className = 'daily-item';
      item.innerHTML = `
        <div class="daily-day">${formatDate(daily.time[i])}</div>
        <div class="daily-icon">${wInfo.icon}</div>
        <div class="daily-bar-wrap">
          <span class="daily-temp-low">${Math.round(low)}°</span>
          <div class="daily-bar">
            <div class="daily-bar-fill" style="margin-left:${leftPct}%; width:${widthPct}%;"></div>
          </div>
          <span class="daily-temp-high">${Math.round(high)}°</span>
        </div>
        <div class="daily-precip">${daily.precipitation_probability_max[i] || 0}%</div>
      `;
      dailyList.appendChild(item);
    }
    
    // Air quality
    if (air && air.current) {
      const a = air.current;
      const aqi = a.us_aqi || 0;
      const aqiInfo = getAQILevel(aqi);
      const aqiCircle = root.querySelector('.air-aqi-circle');
      aqiCircle.style.color = aqiInfo.color;
      root.querySelector('#aqiValue').textContent = Math.round(aqi);
      root.querySelector('#airLevel').textContent = aqiInfo.level;
      root.querySelector('#airLevel').style.color = aqiInfo.color;
      root.querySelector('#airDesc').textContent = aqiInfo.desc;
      root.querySelector('#pm25').textContent = (a.pm2_5 || 0).toFixed(1);
      root.querySelector('#pm10').textContent = (a.pm10 || 0).toFixed(1);
      root.querySelector('#o3').textContent = (a.ozone || 0).toFixed(1);
      root.querySelector('#no2').textContent = (a.nitrogen_dioxide || 0).toFixed(1);
      root.querySelector('#so2').textContent = (a.sulphur_dioxide || 0).toFixed(1);
      root.querySelector('#co').textContent = ((a.carbon_monoxide || 0) / 1000).toFixed(1);
    }
    
    // Update time
    const d = new Date();
    root.querySelector('#updateTime').textContent = `${d.getHours()}:${String(d.getMinutes()).padStart(2,'0')}`;
    
    // Show content
    root.querySelector('#loading').style.display = 'none';
    root.querySelector('#content').style.display = 'block';
  }
  
  async function loadCity(city) {
    try {
      root.querySelector('#loading').style.display = 'block';
      root.querySelector('#content').style.display = 'none';
      root.querySelector('#error').style.display = 'none';
      
      const loc = await geocode(city);
      const [weather, air] = await Promise.all([
        fetchWeather(loc.latitude, loc.longitude),
        fetchAirQuality(loc.latitude, loc.longitude).catch(() => null)
      ]);
      renderWeather(loc, weather, air);
      
      // Save last city
      await ambient.model.set({ lastCity: city });
    } catch (e) {
      root.querySelector('#loading').style.display = 'none';
      root.querySelector('#error').style.display = 'block';
      root.querySelector('#errorMsg').textContent = '出错了: ' + e.message;
    }
  }
  
  function loadByCoords(lat, lon) {
    // Reverse geocoding using weather data only
    (async () => {
      try {
        root.querySelector('#loading').style.display = 'block';
        root.querySelector('#content').style.display = 'none';
        root.querySelector('#error').style.display = 'none';
        const [weather, air] = await Promise.all([
          fetchWeather(lat, lon),
          fetchAirQuality(lat, lon).catch(() => null)
        ]);
        const loc = { 
          name: '当前位置', 
          country: '', 
          admin1: `${lat.toFixed(2)}°, ${lon.toFixed(2)}°`,
          latitude: lat,
          longitude: lon
        };
        renderWeather(loc, weather, air);
      } catch (e) {
        root.querySelector('#loading').style.display = 'none';
        root.querySelector('#error').style.display = 'block';
        root.querySelector('#errorMsg').textContent = '出错了: ' + e.message;
      }
    })();
  }
  
  // Event handlers
  root.querySelector('#citySearch').addEventListener('keypress', (e) => {
    if (e.key === 'Enter' && e.target.value.trim()) {
      loadCity(e.target.value.trim());
    }
  });
  
  root.querySelector('#geoBtn').addEventListener('click', () => {
    if (navigator.geolocation) {
      root.querySelector('#loading').style.display = 'block';
      navigator.geolocation.getCurrentPosition(
        pos => loadByCoords(pos.coords.latitude, pos.coords.longitude),
        err => {
          root.querySelector('#loading').style.display = 'none';
          alert('无法获取位置: ' + err.message);
        }
      );
    } else {
      alert('浏览器不支持定位');
    }
  });
  
  // Initial load
  (async () => {
    const data = await ambient.model.get();
    const initialCity = data.lastCity || '北京';
    loadCity(initialCity);
  })();
})();