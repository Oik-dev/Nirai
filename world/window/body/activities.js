// 世界の活動（どこで何をするか）の正本。名前は本人が選ぶ語、placeは描画が座標にする意味の名前。海と窓が同じものを使う。
export const ACTIVITIES = Object.freeze({
  '居場所でくつろぐ': Object.freeze({ place: '居場所' }),
  '砂地で休む': Object.freeze({ place: '砂地' }),
  '水面の近くで漂う': Object.freeze({ place: '水面の近く' }),
  '海の中を泳ぐ': Object.freeze({ place: '海の中' }),
  '窓辺にいる': Object.freeze({ place: '窓辺' }),
});

// まだ何も選んでいないときと、眠る場所。
export const HOME_ACTIVITY = '居場所でくつろぐ';
