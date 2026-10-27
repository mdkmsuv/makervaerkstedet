import ImageTracer from 'imagetracerjs';

self.onmessage=({data:{id,pixels,width,height,threshold,smoothing}})=>{
  try{
    const data=new Uint8ClampedArray(pixels);
    for(let i=0;i<data.length;i+=4){const a=data[i+3]/255,gray=(data[i]*.2126+data[i+1]*.7152+data[i+2]*.0722)*a+255*(1-a),v=gray<threshold?0:255;data[i]=data[i+1]=data[i+2]=v;data[i+3]=255;}
    const options={pal:[{r:0,g:0,b:0,a:255},{r:255,g:255,b:255,a:255}],colorsampling:0,colorquantcycles:1,layering:0,ltres:smoothing,qtres:smoothing,pathomit:Math.max(4,smoothing*4),rightangleenhance:smoothing<2,strokewidth:0,roundcoords:2,viewbox:true};
    const traced=ImageTracer.imagedataToTracedata({width,height,data},options);
    traced.layers[1]=[];
    self.postMessage({id,svg:ImageTracer.getsvgstring(traced,options)});
  }catch(e){self.postMessage({id,error:e.message});}
};
