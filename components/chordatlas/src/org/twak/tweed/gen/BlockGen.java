package org.twak.tweed.gen;

import java.awt.Color;
import java.awt.Dimension;
import java.awt.Image;
import java.awt.event.ActionEvent;
import java.awt.event.ActionListener;
import java.io.BufferedWriter;
import java.io.File;
import java.io.FileWriter;
import java.io.IOException;
import java.nio.file.FileVisitResult;
import java.nio.file.FileVisitor;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.SimpleFileVisitor;
import java.nio.file.attribute.BasicFileAttributes;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.atomic.AtomicInteger;

import javax.swing.JButton;
import javax.swing.JComponent;
import javax.swing.ImageIcon;
import javax.swing.JLabel;
import javax.swing.JOptionPane;
import javax.swing.JPanel;
import javax.swing.JScrollPane;
import javax.swing.SwingUtilities;
import javax.swing.JTextArea;
import javax.vecmath.Point2d;
import javax.vecmath.Point3d;
import javax.vecmath.Vector2d;

import org.twak.tweed.ClickMe;
import org.twak.tweed.Tweed;
import org.twak.tweed.TweedSettings;
import org.twak.tweed.gen.skel.SkelGen;
import org.twak.tweed.tools.FacadeTool;
import org.twak.tweed.tools.PlaneTool;
import org.twak.utils.Line;
import org.twak.utils.collections.Loop;
import org.twak.utils.collections.LoopL;
import org.twak.utils.collections.Loopable;
import org.twak.utils.collections.Loopz;
import org.twak.utils.collections.SuperLoop;
import org.twak.utils.geom.DRectangle;
import org.twak.utils.geom.ObjDump;
import org.twak.utils.geom.ObjRead;
import org.twak.viewTrace.FacadeFinder;
import org.twak.viewTrace.FacadeFinder.FacadeMode;
import org.twak.viewTrace.Slice;
import org.twak.viewTrace.SliceParameters;
import org.twak.viewTrace.SliceSolver;
import org.twak.viewTrace.facades.AlignStandalone2d;

import com.jme3.asset.ModelKey;
import com.jme3.scene.Spatial;
import com.thoughtworks.xstream.XStream;
import com.vividsolutions.jts.geom.Envelope;
import com.vividsolutions.jts.index.quadtree.Quadtree;

public class BlockGen extends ObjGen {

	File root;
	String selectedSelectedName = "";
	public LoopL<Point3d> polies;
	public transient ProfileGen profileGen;
	
	private static SliceParameters P = new SliceParameters(10); // when set by Slice UI, used for all future blocks!

	public Point2d center;
	protected PlanesGen extraSweeps;
	private transient SelectedBlockPanoramaService selectedBlockPanoramaService;
	private transient volatile String selectedBlockPanoramaStatus =
			"Ready: acquire panoramas from this block's footprint geometry.";
	private transient JLabel selectedBlockPanoramaStatusLabel;
	private transient JButton selectedBlockPanoramaButton;
	
	public BlockGen( File l, Tweed tweed, LoopL<Point3d> polies ) {
		
		super ( new File(l, GISGen.CROPPED_OBJ ).getPath().substring( Tweed.JME.length() ), tweed);

		this.polies = polies;
		this.root = l;
		this.name = "block";
		this.transparency = 0;
		
		this.center = Loopz.average( Loopz.to2dLoop( polies, 1, null ) );
		System.out.println("creating block with name: " + nameCoords() );
	}
	
	@Override
	public void calculate() {
		
		super.calculate();
		doClicked(gNode);
	}

	private void doClicked( Spatial s ) {
		s.setUserData( ClickMe.class.getSimpleName(), new Object[] { new ClickMe() {
			@Override
			public void clicked( Object data ) {
				tweed.frame.setSelected( BlockGen.this );
			}
		} } );
	}

	private void show (String file) {
		String full = new File (root, file).getPath();
		String neuFilename = full.substring( Tweed.JME.length() );
		
		if (!neuFilename.equals( filename )) {
			filename = neuFilename;
			tweed.getAssetManager().deleteFromCache( new ModelKey( filename ) );
			calculateOnJmeThread();
		}
	}
	
	@Override
	public JComponent getUI() {
		
		JPanel panel = (JPanel) super.getUI();

		JButton profiles = new JButton ("find profiles");
		profiles.addActionListener( e -> doProfile() );

		selectedBlockPanoramaButton = new JButton ("get Street View panoramas");
		selectedBlockPanoramaButton.setToolTipText(
				"Plan cameras from this selected block, download one sample, then ask before the batch" );
		selectedBlockPanoramaButton.addActionListener( e -> acquireStreetViewPanos() );
		selectedBlockPanoramaStatusLabel = new JLabel( selectedBlockPanoramaStatus );
		
		JButton panos = new JButton ("render panoramas");
		panos.addActionListener( e -> renderPanos() );
		
		JButton features = new JButton ("find image features");
		features.addActionListener( e -> segnetFacade() );
		
		JButton windows = new JButton ("show windows");
		windows.addActionListener( e -> tweed.frame.addGen( new WindowGen( tweed, this ), true ) );
		
		JButton viewFeatures = new JButton ("features viewer");
		viewFeatures.addActionListener( e -> viewFeatures() );
		
		JButton slice = new JButton ("slice");
		slice.addActionListener( new ActionListener() {
			
			@Override
			public void actionPerformed( ActionEvent e ) {
				new Thread() {
					public void run() {

						File fs = getSlicedFile();

						if ( !fs.exists() ) 
						{
							new SliceSolver( fs, 
									new Slice( 
											getCroppedFile(), 
											getGISFile(), P, false ), P );
						}
						
						tweed.frame.addGen( new ObjGen( 
								tweed.makeWorkspaceRelative( fs ).toString(),
								tweed ), true); 
					}

				}.start();
			}
		} );
		
		JButton tooD = new JButton( "slice UI" );
		tooD.addActionListener( new ActionListener() {
			@Override
			public void actionPerformed( ActionEvent e ) {
				new Slice( root, ProfileGen.SLICE_SCALE );
			}
		} );
		
		JButton loadSln = new JButton( "load last solution" );
		loadSln.addActionListener( new ActionListener() {
			@Override
			public void actionPerformed( ActionEvent e ) {
				File f = getSolutionFile();
				if ( f.exists() ) {

					SolverState SS = (SolverState) new XStream().fromXML( f );
					SkelFootprint.postProcesss( SS );
					
					tweed.frame.addGen( new SkelGen( SS.mesh, tweed, BlockGen.this ), true );
				} else {
					JOptionPane.showMessageDialog( tweed.frame(), "Unable to find pre-computed solution.\n" + f );
				}
			}
		} );
		
		StringBuilder sb = new StringBuilder();
		sb.append( "name:" +nameCoords()+"\nlot info:\n" );
		Optional<Gen> hg = tweed.frame.getGensOf( LotInfoGen.class ).stream().findAny();
		
		if ( hg.isPresent() )
			for ( Loop<Point3d> loop : polies )
				try {
					SuperLoop<Point3d> sl = (SuperLoop) loop;
					for ( Map.Entry<String, Object> e : sl.properties.entrySet() )
						sb.append( " >" + e.getKey() + " : " + e.getValue() + "\n" );
				}
				catch (Throwable th) {th.printStackTrace(  ); }
		

		
		JButton b = new JButton("street widths");
		b.addActionListener( e -> findWidths(polies, tweed.frame.getGenOf( GISGen.class )) );
		
		
		JButton splits = new JButton("suggest sweep edges");
		splits.addActionListener( new ActionListener() {

			@Override
			public void actionPerformed( ActionEvent e ) {
				
				if (extraSweeps == null) {
					extraSweeps = new PlanesGen(tweed);
					extraSweeps.name = "sweeps for " + BlockGen.this.name;
					tweed.frame.addGen( extraSweeps, true );
				}
				
				PlaneTool pt = new PlaneTool( tweed, extraSweeps );
				tweed.setTool( pt );
			}
		});
		
		JTextArea name = new JTextArea( sb.toString() );
		name.setEditable( false );
		JScrollPane nameScroller = new JScrollPane( name );
		nameScroller.setPreferredSize( new Dimension( 100, 150 ) );
		
		panel.add( b );
		panel.add(profiles, 0 );
		panel.add(selectedBlockPanoramaButton, 1 );
		panel.add(selectedBlockPanoramaStatusLabel, 2 );
		panel.add(panos, 3 );
		panel.add(features, 4 );
		panel.add( windows, 5 );
		panel.add(new JLabel("other:"), 6 );
		panel.add( slice );
		panel.add( viewFeatures );
		if (getSolutionFile().exists())
			panel.add( loadSln );
		panel.add(new JLabel("metadata:") );
		panel.add( nameScroller );
		panel.add( splits );
		
		
		return panel;
	}

	private void acquireStreetViewPanos() {
		if ( selectedBlockPanoramaService == null )
			selectedBlockPanoramaService = new SelectedBlockPanoramaService();
		setPanoramaAcquisitionRunning( true );
		selectedBlockPanoramaService.submit( root, polies, new SelectedBlockPanoramaService.Callback() {
			@Override public void statusChanged( String status ) {
				requireEdt();
				setPanoramaAcquisitionStatus( status );
			}

			@Override public void sampleReady( SelectedBlockPanoramaService.Sample sample,
					SelectedBlockPanoramaService.Approval approval ) {
				requireEdt();
				int width = sample.preview.getWidth(), height = sample.preview.getHeight();
				double scale = Math.min( 1d, Math.min( 720d / width, 360d / height ) );
				Image preview = sample.preview.getScaledInstance(
						Math.max( 1, (int)Math.round( width * scale ) ),
						Math.max( 1, (int)Math.round( height * scale ) ), Image.SCALE_SMOOTH );
				Object[] message = new Object[] {
						"One panorama was downloaded from cameras planned only around this selected block.",
						new JLabel( new ImageIcon( preview ) ),
						"Sample: " + sample.image,
						"Download the remaining planned panoramas?"
				};
				int answer = JOptionPane.showConfirmDialog( tweed.frame(), message,
						"Approve selected-block Street View batch", JOptionPane.YES_NO_OPTION,
						JOptionPane.QUESTION_MESSAGE );
				if ( answer == JOptionPane.YES_OPTION )
					approval.approve();
				else
					approval.cancel();
			}

			@Override public void completed( SelectedBlockPanoramaService.Result result ) {
				requireEdt();
				try {
					refreshOrCreatePanoramaLayer();
					setPanoramaAcquisitionStatus( "Ready: selected-block panoramas loaded (new "
							+ result.succeeded + ", cached " + result.existing + ")." );
					JOptionPane.showMessageDialog( tweed.frame(),
							"Street View panoramas are ready and the PanoGen layer was refreshed.\n"
							+ "Folder: " + result.panoramaFolder + "\nReport: " + result.report );
				} catch ( IOException layerError ) {
					showPanoramaError( "Panoramas were downloaded, but their layer could not be refreshed:\n"
							+ layerError.getMessage() + "\nFolder: " + result.panoramaFolder );
				} finally {
					setPanoramaAcquisitionRunning( false );
				}
			}

			@Override public void cancelled( SelectedBlockPanoramaService.Sample sample ) {
				requireEdt();
				setPanoramaAcquisitionRunning( false );
				setPanoramaAcquisitionStatus( "Sample retained; remaining panorama batch was not approved." );
			}

			@Override public void failed( SelectedBlockPanoramaService.Failure failure ) {
				requireEdt();
				setPanoramaAcquisitionRunning( false );
				setPanoramaAcquisitionStatus( "Street View acquisition failed; see report/log." );
				showPanoramaError( failure.details() );
			}
		} );
	}

	private void refreshOrCreatePanoramaLayer() throws IOException {
		requireEdt();
		File panoramaFolder = new File( Tweed.DATA, "panos" ).getCanonicalFile();
		if ( !panoramaFolder.isDirectory() )
			throw new IOException( "Panorama folder not found: " + panoramaFolder );

		for ( Gen gen : tweed.frame.getGensOf( PanoGen.class ) ) {
			PanoGen candidate = (PanoGen) gen;
			if ( candidate.folder != null &&
					Tweed.toWorkspace( candidate.folder ).getCanonicalFile().equals( panoramaFolder ) ) {
				candidate.configureFromWorkspaceManifest();
				candidate.rescan();
				return;
			}
		}

		PanoGen created = new PanoGen( tweed.makeWorkspaceRelative( panoramaFolder ), tweed, Tweed.LAT_LONG );
		created.name = "panos " + panoramaFolder.getName();
		tweed.frame.addGen( created, true ); // addGen schedules calculate on the jME thread.
	}

	private void setPanoramaAcquisitionRunning( boolean running ) {
		if ( selectedBlockPanoramaButton != null )
			selectedBlockPanoramaButton.setEnabled( !running );
		if ( running )
			setPanoramaAcquisitionStatus( "Preparing selected-block Street View acquisition..." );
	}

	private void setPanoramaAcquisitionStatus( String status ) {
		selectedBlockPanoramaStatus = status;
		if ( selectedBlockPanoramaStatusLabel != null ) {
			selectedBlockPanoramaStatusLabel.setText( status );
			selectedBlockPanoramaStatusLabel.setToolTipText( status );
		}
	}

	private void showPanoramaError( String details ) {
		System.err.println( details );
		JOptionPane.showMessageDialog( tweed.frame(), details,
				"Selected-block Street View acquisition failed", JOptionPane.ERROR_MESSAGE );
	}

	private static void requireEdt() {
		if ( !SwingUtilities.isEventDispatchThread() )
			throw new IllegalStateException( "Panorama GUI callback must run on the EDT" );
	}

	public final static String STREET_WIDTH = "streetwidth";
	
	public static void findWidths(LoopL<Point3d> polies, GISGen gisGen ) {

		LoopL<Point2d> polies2d = toXZLoopSameProperties ( polies );

		Map<Point2d, Point2d> onBoundary = new HashMap<>(); 
		
		{
			LoopL<Point2d> boundary = Loopz.removeInnerEdges( polies2d );
			boundary.stream().filter( x -> Loopz.area( x ) > 10).
				flatMap( x -> x.streamAble() ).
				forEach( p -> onBoundary.put( p.get(), p.getNext().get() ) );
		}

//		PaintThing.debug.clear();
		
		gisGen.ensureQuad();
		
		for ( Loop<Point2d> footprint : polies2d ) {
			
			Map<Line, Double> widths = new HashMap<>();
			((SuperLoop)footprint).properties.put( STREET_WIDTH, widths );
			
			for ( Loopable<Point2d> ll : footprint.loopableIterator() ) {
				Point2d a = onBoundary.get( ll.get() );

				if ( a != null && a.equals( ll.getNext().get() ) ) {

					Line l = new Line( ll.get(), ll.getNext().get() );

//					PaintThing.debug( Color.black, 2f, l );
				
					double sw = findStreetWidth ( polies, l, gisGen.quadtree, 30, gisGen );
					
					widths.put( l, sw );
					
//					if (sw < 1e3) {
//						
//						Vector2d dir = l.dir();
//						dir.set( new double[] { -dir.y, dir.x } );
//						dir.normalize();
//						
//						Point2d mid = l.fromPPram( 0.5 );
//						dir.scale( sw );
//						dir.add( mid );
//						PaintThing.debug( Color.black, 1f, new Line( mid, new Point2d( dir ) ) );
//					}
				}

			}
		}
		
//		new Plot ( polies2d );
	}

	private static LoopL<Point2d> toXZLoopSameProperties(LoopL<Point3d> list) {
		
		LoopL<Point2d> out = new LoopL<>();
		
		for (Loop<Point3d> ll : list)
			out.add( toXZLoopSameProperties( ll) );
		
		return out;
	}
	
	private static Loop<Point2d>  toXZLoopSameProperties( Loop<Point3d> ll) {
		
		Loop<Point2d> o;
		
		if (ll instanceof SuperLoop ) {
			o = new SuperLoop( (String) ( (SuperLoop)ll).properties.get( "name" ) );
			((SuperLoop)o).properties = ( (SuperLoop) ll ).properties;
		}
		else {
			o = new Loop<>();
		}
		
		for (Point3d p : ll) 
			o.append(new Point2d(p.x, p.z));
		
		for (Loop<Point3d> hole : ll.holes)
			o.holes.add(toXZLoopSameProperties(hole));
		
		return o;
	}
	
	private static final int MIN_SW = 15;
	private synchronized static double findStreetWidth( LoopL<Point3d> ignore, Line l, Quadtree quadtree, double max, GISGen gisGen ) {
		
		if (l.length() < MIN_SW) {
			Point2d cen = l.fromPPram( 0.5 );
			Vector2d up = l.dir();
			up.scale( MIN_SW  / (2*up.length() ) );
			l = new Line(new Point2d ( cen ), new Point2d ( cen ) );
			l.end.add( up );
			l.start.sub( up);
		}
		
		Vector2d dir = l.dir();
		dir.set( new double[] { -dir.y, dir.x } );
		dir.scale( max / l.length() );
		
		DRectangle dr =  new DRectangle(l.start );
		dr.envelop( l.end );
		
		Point2d a = new Point2d(l.start), b = new Point2d( l.end );
		a.add( dir ); b.add( dir );
		dr.envelop( a ); dr.envelop( b );
		
		double dist = Double.MAX_VALUE;

		
		Loop<Point2d> queryBounds = new Loop<>(l.end, l.start, a, b);
		Envelope queryEnvelope = new Envelope( dr.x, dr.getMaxX(), dr.y, dr.getMaxY()  );
		
		for (Object o : quadtree.query( queryEnvelope ) ) {
			
			Loop<Point3d> block = (Loop)o;
			
//			why is block sometime null?
			if ( block == null || ignore.contains( block ) || ! queryEnvelope.intersects( GISGen.envelope( block ) ) )
				continue;
			
			for (Loopable<Point3d> pt : block.loopableIterator()) {
				Line query = new Line ( Pointz.to2XZ( pt.get() ) , Pointz.to2XZ( pt.getNext().get()));
				
				if ( !Loopz.inside( query, queryBounds ) )
					continue;
				
				dist = Math.min( dist, l.distance( query ) );
			}
		}
		
		return dist;
	}

	private void viewFeatures() {
		AlignStandalone2d.show( getInputFolder( FeatureCache.FEATURE_FOLDER ).toString() );
	}

	private void segnetFacade() {
		
		File r = getInputFolder( FeatureCache.FEATURE_FOLDER );
		
		if (!r.exists()) {
			JOptionPane.showMessageDialog( tweed.frame(), "no facade images found - have they been rendered?" );
			return;
		}
			
		
		File toProcess = new File (r, "files.txt");
		
		StringBuffer sb = new StringBuffer();
		List<File> expectedResults = new ArrayList<>();
		
		FileVisitor<Path> fv = new SimpleFileVisitor<Path>() {
			@Override
			public FileVisitResult visitFile( Path file, BasicFileAttributes attrs ) throws IOException {
				File f = file.toFile();
				File result = new File ( f.getParentFile(), FeatureCache.PARAMETERS_YML );
				
				if (f.getName().equals( FeatureCache.RENDERED_IMAGE_PNG ) && !result.exists() ) {
					
					Path resultR = r.toPath().relativize( result.toPath() );
					String inputPath = f.getAbsolutePath();

					if ( inputPath.matches( ".*\\s+.*" ) || resultR.toString().matches( ".*\\s+.*" ) )
						throw new IOException( "facade_pytorch file lists do not support whitespace in paths: " + inputPath );

					sb.append( inputPath ).append( "\t" ).append( resultR ).append( System.lineSeparator() );
					expectedResults.add( result );
				}
				
				return FileVisitResult.CONTINUE;
			}
		};

		
		try {
			Files.walkFileTree( r.toPath(), fv );
			FileWriter fw = new FileWriter( toProcess );
			fw.append( sb );
			fw.close();
		} catch ( IOException e ) {
			e.printStackTrace();
			JOptionPane.showMessageDialog( tweed.frame(), "unable to prepare facade_pytorch input:\n" + e.getMessage() );
			return;
		}
		
		if (sb.length() == 0) {
			if ( tweed.features != null )
				tweed.features.refresh();
			JOptionPane.showMessageDialog( tweed.frame(), "all features already computed. nothing to do here!" );
			return;
		}
		
		System.out.println( "running CNN to find features..." );

		File conda = new File( TweedSettings.settings.condaExecutable );
		File facadePackage = new File( TweedSettings.settings.facadePytorchRoot );
		File facadeProject = facadePackage.getParentFile();

		if ( !conda.isFile() ) {
			JOptionPane.showMessageDialog( tweed.frame(), "Conda executable not found:\n" + conda );
			return;
		}
		if ( facadeProject == null || !new File( facadePackage, "__main__.py" ).isFile() ) {
			JOptionPane.showMessageDialog( tweed.frame(), "facade_pytorch package not found:\n" + facadePackage );
			return;
		}

		File log = new File( r, "facade-pytorch.log" );
		ProcessBuilder pb = new ProcessBuilder(
				conda.getAbsolutePath(), "run", "--no-capture-output",
				"-n", TweedSettings.settings.condaEnvironment,
				"python", "-B", "-m", "facade_pytorch",
				"--output", r.getAbsolutePath(),
				"--files", toProcess.getAbsolutePath() );
		pb.directory( facadeProject );
		pb.redirectErrorStream( true );
		pb.redirectOutput( ProcessBuilder.Redirect.appendTo( log ) );
		pb.environment().put( "PYTHONUNBUFFERED", "1" );

		new Thread( () -> {
			try {
				Process p = pb.start();
				int exitCode = p.waitFor();
				List<File> missing = new ArrayList<>();
				for ( File expected : expectedResults )
					if ( !expected.isFile() )
						missing.add( expected );

				if ( exitCode == 0 && missing.isEmpty() ) {
					if ( tweed.features != null )
						tweed.features.refresh();
					System.out.println( "facade_pytorch completed successfully; log: " + log );
					SwingUtilities.invokeLater( () -> JOptionPane.showMessageDialog( tweed.frame(),
							"facade features completed successfully.\nLog: " + log ) );
				}
				else {
					String detail = "facade_pytorch failed (exit code " + exitCode + ")" +
							( missing.isEmpty() ? "" : "; missing " + missing.size() + " parameters.yml file(s)" ) +
							".\nFull log: " + log;
					System.err.println( detail );
					SwingUtilities.invokeLater( () -> JOptionPane.showMessageDialog( tweed.frame(), detail ) );
				}
			} catch ( IOException e ) {
				e.printStackTrace();
				SwingUtilities.invokeLater( () -> JOptionPane.showMessageDialog( tweed.frame(),
						"unable to start facade_pytorch:\n" + e.getMessage() + "\nLog: " + log ) );
			} catch ( InterruptedException e ) {
				Thread.currentThread().interrupt();
			}
		}, "facade-pytorch" ).start();
		
	}

	private void renderPanos() {
		
		if (getInputFolder( FeatureCache.FEATURE_FOLDER ).exists()) {
			int result = JOptionPane.showConfirmDialog(tweed.frame(), "feature folder already exists. really re-render?",
			        "alert", JOptionPane.OK_CANCEL_OPTION);
			if (result == JOptionPane.CANCEL_OPTION)
				return;
		}
		
		FacadeFinder.facadeMode = FacadeMode.PER_CAMERA;
		
		try {
			FacadeTool ff = new FacadeTool(tweed);
			ff.singleFolder = false;
			ff.renderFacade( polies, new AtomicInteger( 0 ), new BufferedWriter(new FileWriter( Tweed.SCRATCH +"/params.txt" )), null );
		} catch ( IOException e ) {
			e.printStackTrace();
		}
	}

	public String nameCoords() {
		return center.x+"_"+center.y;
	}
	
	private void doProfile() {
		new Thread() {
			@Override
			public void run() {
				profileGen = new ProfileGen(BlockGen.this, Loopz.toXZLoop( polies ), tweed);
			}
		}.start();
	}
	
	public File getGISFile() {
		return new File( root, "gis.obj" );
	}

	public File getCroppedFile() {
		return new File( root, "cropped.obj" );
	}
	
	public File getSlicedFile() {
		return new File( root, "sliced.obj" );
	};
	
	ObjRead croppedMesh = null;
	public ObjRead getCroppedMesh() {
		
		if (croppedMesh == null)
			croppedMesh = new ObjRead( getCroppedFile() );

		return croppedMesh;
	}
	
	double[] croppedExtent = null;
	public double[] getCroppedExtent() {
		if (croppedExtent == null)
			croppedExtent = getCroppedMesh().findExtent();
		
		return croppedExtent;
	}
	
	@Override
	public void dumpObj( ObjDump dump ) {
		dump.setCurrentMaterial( Color.blue, 0.5);
		dump.addAll (getCroppedMesh());
	}

	public File getInputFolder( String dir ) {
		return new File (Tweed.DATA, dir+File.separator+nameCoords() );
	}

	/** Root directory for this selected block's published inputs and references. */
	public File getRoot() {
		return root;
	}
	
	public File getSolutionFile() {
		return new File (getInputFolder(ResultsGen.SOLUTIONS),ResultsGen.SOLVER_FILE);
	}
}
