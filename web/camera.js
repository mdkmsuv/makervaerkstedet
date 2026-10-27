// Teacher-requested viewing. Capturing a picture never clears a plate.
let cameraId=null,cameraBusy=false,cameraBlob=null,cameraRequest=0;
const cam=id=>document.getElementById(id);
const currentCamera=(id,request)=>cameraId===id&&cameraRequest===request&&!cam('camera-panel').hidden;
function clearCameraPhoto(){
  cam('camera-photo').hidden=true;cam('camera-photo').removeAttribute('src');
  if(cameraBlob)URL.revokeObjectURL(cameraBlob);cameraBlob=null;
}
async function captureTeacherCamera(){
  if(cameraBusy||!cameraId)return;
  const id=cameraId,request=++cameraRequest;
  cameraBusy=true;cam('camera-capture').disabled=true;cam('camera-error').hidden=true;
  clearCameraPhoto();cam('camera-updated').textContent='';
  cam('camera-state').textContent='Henter et nyt billede fra printeren…';
  try{
    const data=await api('/api/camera/capture',{id},true);
    if(!currentCamera(id,request))return;
    const response=await fetch(`/api/camera/image/${encodeURIComponent(id)}`,{credentials:'same-origin',cache:'no-store'});
    if(response.status===401)setTeacherSession(false);
    if(!response.ok)throw new Error('Kamerabilledet kunne ikke vises.');
    const blob=await response.blob();
    if(!currentCamera(id,request))return;
    cameraBlob=URL.createObjectURL(blob);cam('camera-photo').src=cameraBlob;cam('camera-photo').hidden=false;
    cam('camera-photo').alt=`Kamerabillede fra ${printerName(id)}`;
    cam('camera-updated').textContent=`Billede taget ${new Date(data.latest.captured*1000).toLocaleString('da-DK')}`;
    cam('camera-state').textContent=data.message;
  }catch(error){
    if(currentCamera(id,request)){cam('camera-error').textContent=error.message;cam('camera-error').hidden=false;cam('camera-state').textContent='Der kunne ikke hentes et nyt billede.';}
  }finally{
    if(currentCamera(id,request)){cameraBusy=false;cam('camera-capture').disabled=false;}
  }
}
cam('teacher-printers').addEventListener('click',async event=>{
  const button=event.target.closest('[data-camera]');if(!button||cameraBusy)return;
  cameraId=button.dataset.camera;clearCameraPhoto();cam('camera-panel').hidden=false;
  cam('camera-title').textContent=`${printerName(cameraId)} · Kamera`;
  cam('camera-panel').scrollIntoView({behavior:'smooth',block:'start'});
  await captureTeacherCamera();
});
window.closeTeacherCamera=()=>{
  cameraRequest++;cameraId=null;cameraBusy=false;cam('camera-capture').disabled=false;cam('camera-panel').hidden=true;clearCameraPhoto();
};
cam('camera-close').addEventListener('click',window.closeTeacherCamera);
cam('camera-capture').addEventListener('click',captureTeacherCamera);
